from src.source_monitoring.entity_discovery_models import PrimaryEntityKind
from src.source_monitoring.source_discovery_models import (
    ENTITY_KIND_SOURCE_ROLE_POLICY_VERSION,
    SOURCE_ROLE_ONTOLOGY_VERSION,
    SourceRole,
    SourceRoleDefinition,
)


_ALL_KINDS = tuple(kind.value for kind in PrimaryEntityKind)


_DEFINITIONS: tuple[SourceRoleDefinition, ...] = (
    SourceRoleDefinition(
        source_role=SourceRole.OFFICIAL_HOMEPAGE,
        english_aliases=("homepage", "official website", "home"),
        chinese_aliases=("官网", "官方网站", "主页"),
        description="Primary official web presence for an entity.",
        applicable_primary_entity_kinds=_ALL_KINDS,
        query_terms_by_language={
            "en": ("official website", "homepage"),
            "zh": ("官网", "官方网站"),
        },
        url_path_hints=("", "home", "about"),
    ),
    SourceRoleDefinition(
        source_role=SourceRole.NEWSROOM,
        english_aliases=("newsroom", "news", "media"),
        chinese_aliases=("新闻", "新闻中心", "媒体"),
        description="Official news or media section with durable entity updates.",
        applicable_primary_entity_kinds=(
            PrimaryEntityKind.OPERATING_COMPANY.value,
            PrimaryEntityKind.INVESTMENT_FIRM.value,
            PrimaryEntityKind.PROFESSIONAL_SERVICES_FIRM.value,
            PrimaryEntityKind.ECOSYSTEM_SUPPORT_ORGANIZATION.value,
        ),
        query_terms_by_language={"en": ("newsroom", "news"), "zh": ("新闻", "新闻中心")},
        url_path_hints=("news", "newsroom", "media", "press/news"),
    ),
    SourceRoleDefinition(
        source_role=SourceRole.PRESS_RELEASES,
        english_aliases=("press releases", "press", "announcements"),
        chinese_aliases=("新闻稿", "公告", "发布"),
        description="Official press-release or announcement index.",
        applicable_primary_entity_kinds=(
            PrimaryEntityKind.OPERATING_COMPANY.value,
            PrimaryEntityKind.INVESTMENT_FIRM.value,
            PrimaryEntityKind.PROFESSIONAL_SERVICES_FIRM.value,
            PrimaryEntityKind.PUBLIC_SECTOR_BODY.value,
        ),
        query_terms_by_language={
            "en": ("press releases", "announcements"),
            "zh": ("新闻稿", "公告"),
        },
        url_path_hints=("press", "press-releases", "announcements", "releases"),
    ),
    SourceRoleDefinition(
        source_role=SourceRole.INSIGHTS,
        english_aliases=("insights", "thought leadership", "perspectives"),
        chinese_aliases=("洞察", "观点", "见解"),
        description="Official insights, perspectives, or thought-leadership section.",
        applicable_primary_entity_kinds=(
            PrimaryEntityKind.INVESTMENT_FIRM.value,
            PrimaryEntityKind.PROFESSIONAL_SERVICES_FIRM.value,
            PrimaryEntityKind.INFORMATION_PLATFORM.value,
        ),
        query_terms_by_language={"en": ("insights", "AI insights"), "zh": ("洞察", "观点")},
        url_path_hints=("insights", "perspectives", "thought-leadership"),
    ),
    SourceRoleDefinition(
        source_role=SourceRole.RESEARCH_PUBLICATIONS,
        english_aliases=("research", "publications", "papers"),
        chinese_aliases=("研究", "出版物", "论文"),
        description="Official research, publication, report, or paper index.",
        applicable_primary_entity_kinds=(
            PrimaryEntityKind.OPERATING_COMPANY.value,
            PrimaryEntityKind.PROFESSIONAL_SERVICES_FIRM.value,
            PrimaryEntityKind.PUBLIC_SECTOR_BODY.value,
            PrimaryEntityKind.KNOWLEDGE_INSTITUTION.value,
            PrimaryEntityKind.INFORMATION_PLATFORM.value,
        ),
        query_terms_by_language={
            "en": ("research", "publications"),
            "zh": ("研究", "出版物"),
        },
        url_path_hints=("research", "publications", "papers", "library"),
    ),
    SourceRoleDefinition(
        source_role=SourceRole.CAREERS,
        english_aliases=("careers", "jobs", "recruiting"),
        chinese_aliases=("招聘", "职业", "加入我们"),
        description="Official careers or recruiting hub, not individual job postings.",
        applicable_primary_entity_kinds=(
            PrimaryEntityKind.OPERATING_COMPANY.value,
            PrimaryEntityKind.INVESTMENT_FIRM.value,
            PrimaryEntityKind.PROFESSIONAL_SERVICES_FIRM.value,
            PrimaryEntityKind.ECOSYSTEM_SUPPORT_ORGANIZATION.value,
            PrimaryEntityKind.TALENT_MARKET_PLATFORM.value,
        ),
        query_terms_by_language={"en": ("careers",), "zh": ("招聘", "加入我们")},
        url_path_hints=("careers", "jobs", "join-us", "recruiting"),
    ),
    SourceRoleDefinition(
        source_role=SourceRole.PORTFOLIO,
        english_aliases=("portfolio", "companies", "investments"),
        chinese_aliases=("投资组合", "被投企业", "案例"),
        description="Official portfolio, investment, or member-company section.",
        applicable_primary_entity_kinds=(
            PrimaryEntityKind.INVESTMENT_FIRM.value,
            PrimaryEntityKind.ECOSYSTEM_SUPPORT_ORGANIZATION.value,
        ),
        query_terms_by_language={"en": ("portfolio", "companies"), "zh": ("投资组合", "被投企业")},
        url_path_hints=("portfolio", "companies", "investments"),
    ),
    SourceRoleDefinition(
        source_role=SourceRole.TRANSACTIONS,
        english_aliases=("transactions", "deals", "investments"),
        chinese_aliases=("交易", "投资事件", "项目"),
        description="Official transaction, deal, or investment-activity section.",
        applicable_primary_entity_kinds=(PrimaryEntityKind.INVESTMENT_FIRM.value,),
        query_terms_by_language={"en": ("transactions", "deals"), "zh": ("交易", "投资事件")},
        url_path_hints=("transactions", "deals", "investments"),
    ),
    SourceRoleDefinition(
        source_role=SourceRole.POLICY_UPDATES,
        english_aliases=("policy updates", "regulation", "guidance"),
        chinese_aliases=("政策", "法规", "通知"),
        description="Official policy, regulation, guidance, or public notice section.",
        applicable_primary_entity_kinds=(PrimaryEntityKind.PUBLIC_SECTOR_BODY.value,),
        query_terms_by_language={"en": ("policy updates", "guidance"), "zh": ("政策", "通知")},
        url_path_hints=("policy", "guidance", "regulation", "notices"),
    ),
    SourceRoleDefinition(
        source_role=SourceRole.REPORTS_OR_DATA,
        english_aliases=("reports", "data", "statistics"),
        chinese_aliases=("报告", "数据", "统计"),
        description="Official reports, data, statistics, or dataset section.",
        applicable_primary_entity_kinds=(
            PrimaryEntityKind.PUBLIC_SECTOR_BODY.value,
            PrimaryEntityKind.KNOWLEDGE_INSTITUTION.value,
            PrimaryEntityKind.INFORMATION_PLATFORM.value,
        ),
        query_terms_by_language={"en": ("reports", "data"), "zh": ("报告", "数据")},
        url_path_hints=("reports", "data", "statistics", "datasets"),
    ),
    SourceRoleDefinition(
        source_role=SourceRole.EVENTS_OR_PROGRAMS,
        english_aliases=("events", "programs", "initiatives"),
        chinese_aliases=("活动", "项目", "计划"),
        description="Official events, programs, initiatives, or convening section.",
        applicable_primary_entity_kinds=(
            PrimaryEntityKind.KNOWLEDGE_INSTITUTION.value,
            PrimaryEntityKind.ECOSYSTEM_SUPPORT_ORGANIZATION.value,
        ),
        query_terms_by_language={"en": ("events", "programs"), "zh": ("活动", "项目")},
        url_path_hints=("events", "programs", "initiatives"),
    ),
    SourceRoleDefinition(
        source_role=SourceRole.BLOG,
        english_aliases=("blog", "stories", "articles"),
        chinese_aliases=("博客", "文章", "专栏"),
        description="Official blog or durable article-index section.",
        applicable_primary_entity_kinds=(
            PrimaryEntityKind.OPERATING_COMPANY.value,
            PrimaryEntityKind.KNOWLEDGE_INSTITUTION.value,
            PrimaryEntityKind.INFORMATION_PLATFORM.value,
        ),
        query_terms_by_language={"en": ("blog",), "zh": ("博客", "文章")},
        url_path_hints=("blog", "stories", "articles"),
    ),
    SourceRoleDefinition(
        source_role=SourceRole.OTHER_OFFICIAL_SECTION,
        english_aliases=("official section", "resources"),
        chinese_aliases=("官方栏目", "资源"),
        description="Other durable official section that does not fit a narrower controlled role.",
        applicable_primary_entity_kinds=_ALL_KINDS,
        query_terms_by_language={"en": ("resources",), "zh": ("资源",)},
        url_path_hints=("resources", "about", "official"),
    ),
)


_ROLE_BY_CODE = {item.source_role: item for item in _DEFINITIONS}

_ROLE_POLICY: dict[PrimaryEntityKind, tuple[SourceRole, ...]] = {
    PrimaryEntityKind.INVESTMENT_FIRM: (
        SourceRole.OFFICIAL_HOMEPAGE,
        SourceRole.PORTFOLIO,
        SourceRole.INSIGHTS,
        SourceRole.NEWSROOM,
        SourceRole.PRESS_RELEASES,
        SourceRole.CAREERS,
    ),
    PrimaryEntityKind.PROFESSIONAL_SERVICES_FIRM: (
        SourceRole.OFFICIAL_HOMEPAGE,
        SourceRole.INSIGHTS,
        SourceRole.RESEARCH_PUBLICATIONS,
        SourceRole.NEWSROOM,
        SourceRole.PRESS_RELEASES,
        SourceRole.CAREERS,
    ),
    PrimaryEntityKind.OPERATING_COMPANY: (
        SourceRole.OFFICIAL_HOMEPAGE,
        SourceRole.NEWSROOM,
        SourceRole.PRESS_RELEASES,
        SourceRole.BLOG,
        SourceRole.RESEARCH_PUBLICATIONS,
        SourceRole.CAREERS,
    ),
    PrimaryEntityKind.PUBLIC_SECTOR_BODY: (
        SourceRole.OFFICIAL_HOMEPAGE,
        SourceRole.POLICY_UPDATES,
        SourceRole.REPORTS_OR_DATA,
        SourceRole.PRESS_RELEASES,
        SourceRole.RESEARCH_PUBLICATIONS,
    ),
    PrimaryEntityKind.KNOWLEDGE_INSTITUTION: (
        SourceRole.OFFICIAL_HOMEPAGE,
        SourceRole.RESEARCH_PUBLICATIONS,
        SourceRole.REPORTS_OR_DATA,
        SourceRole.EVENTS_OR_PROGRAMS,
        SourceRole.BLOG,
    ),
    PrimaryEntityKind.INFORMATION_PLATFORM: (
        SourceRole.OFFICIAL_HOMEPAGE,
        SourceRole.REPORTS_OR_DATA,
        SourceRole.INSIGHTS,
        SourceRole.RESEARCH_PUBLICATIONS,
        SourceRole.BLOG,
    ),
    PrimaryEntityKind.ECOSYSTEM_SUPPORT_ORGANIZATION: (
        SourceRole.OFFICIAL_HOMEPAGE,
        SourceRole.EVENTS_OR_PROGRAMS,
        SourceRole.PORTFOLIO,
        SourceRole.NEWSROOM,
        SourceRole.CAREERS,
    ),
    PrimaryEntityKind.TALENT_MARKET_PLATFORM: (
        SourceRole.OFFICIAL_HOMEPAGE,
        SourceRole.CAREERS,
        SourceRole.BLOG,
        SourceRole.REPORTS_OR_DATA,
    ),
}


def get_source_role_ontology() -> tuple[SourceRoleDefinition, ...]:
    return _DEFINITIONS


def get_source_role_definition(source_role: SourceRole) -> SourceRoleDefinition:
    return _ROLE_BY_CODE[source_role]


def applicable_source_roles(
    primary_entity_kind: PrimaryEntityKind,
) -> tuple[SourceRole, ...]:
    roles = _ROLE_POLICY.get(
        primary_entity_kind,
        (SourceRole.OFFICIAL_HOMEPAGE, SourceRole.OTHER_OFFICIAL_SECTION),
    )
    if SourceRole.OTHER_OFFICIAL_SECTION not in roles:
        roles = roles + (SourceRole.OTHER_OFFICIAL_SECTION,)
    return roles


def source_role_policy_snapshot() -> dict[str, object]:
    return {
        "policy_version": ENTITY_KIND_SOURCE_ROLE_POLICY_VERSION,
        "ontology_version": SOURCE_ROLE_ONTOLOGY_VERSION,
        "mapping": {
            kind.value: [role.value for role in applicable_source_roles(kind)]
            for kind in sorted(_ROLE_POLICY, key=lambda item: item.value)
        },
    }
