import re
import unicodedata
from collections import defaultdict

from src.source_monitoring.models import EntityTypeDefinition


ENTITY_TYPE_ONTOLOGY_VERSION = "entity_type_ontology_v1_1"

PROHIBITED_ENTITY_TYPE_CODES = {
    "search_api",
    "rss",
    "selected_website",
}

ENTITY_TYPE_JUSTIFICATIONS: dict[str, str] = {
    "technology_company": "Technology-company strategic moves and AI product roles.",
    "ai_native_company": "AI investment, AI partnerships, and AI product strategy needs.",
    "enterprise_software_company": "Enterprise AI adoption and digital transformation needs.",
    "industrial_technology_company": "Industry research themes and AI adoption in operating sectors.",
    "growth_stage_company": "Strategic moves, team expansion, and venture-backed career signals.",
    "venture_capital_firm": "VC analyst requirements, investment trends, fundraising, and hiring signals.",
    "corporate_venture_capital": "Corporate venture activity, AI partnerships, and investment signals.",
    "private_equity_or_growth_equity_firm": "Growth equity fundraising and investment-sector career paths.",
    "portfolio_company": "Venture-backed operating-company signals and career bridge paths.",
    "startup_accelerator": "Early-stage ecosystem signals, startup paths, and fellowship opportunities.",
    "venture_studio": "Startup creation, venture-building, and operating-company signals.",
    "management_consulting_firm": "Strategy consulting entry routes and thought leadership.",
    "technology_consulting_firm": "AI transformation consulting and enterprise implementation signals.",
    "boutique_investment_bank_or_fa": "Boutique FA paths and FA-to-VC transition signals.",
    "research_institute": "Industry research, AI adoption, and emerging research themes.",
    "policy_think_tank": "Technology policy and regulatory intelligence needs.",
    "industry_association": "Sector adoption, regulation, and market trend signals.",
    "government_or_regulatory_body": "Technology and venture-capital regulatory change signals.",
    "professional_media": "Professional market, industry, funding, and career intelligence signals.",
    "investment_data_provider": "Investment trend, funding, and market data needs.",
    "recruiting_platform": "Internship, fellowship, and talent-market signal needs.",
}

ENTITY_TYPE_ONTOLOGY: tuple[EntityTypeDefinition, ...] = (
    EntityTypeDefinition(
        code="technology_company",
        display_name="Technology Company",
        definition=(
            "An operating company whose products, services, or business model "
            "are materially technology-driven."
        ),
        broader_group="operating_company",
        aliases=(
            "tech company",
            "technology firm",
            "digital company",
            "科技公司",
            "科技企业",
            "技术公司",
        ),
        example_signal_domains=(
            "new product lines",
            "market expansion",
            "corporate strategy hiring signals",
        ),
        ontology_version=ENTITY_TYPE_ONTOLOGY_VERSION,
    ),
    EntityTypeDefinition(
        code="ai_native_company",
        display_name="AI-Native Company",
        definition=(
            "A technology company whose core product, workflow, or value "
            "proposition is built around artificial intelligence."
        ),
        broader_group="operating_company",
        aliases=(
            "ai company",
            "ai-native company",
            "artificial intelligence startup",
            "AI原生公司",
            "人工智能原生公司",
            "AI创业公司",
        ),
        example_signal_domains=(
            "AI product launches",
            "AI funding activity",
            "AI strategy team expansion",
        ),
        ontology_version=ENTITY_TYPE_ONTOLOGY_VERSION,
    ),
    EntityTypeDefinition(
        code="enterprise_software_company",
        display_name="Enterprise Software Company",
        definition=(
            "A technology company building software products for business, "
            "institutional, or enterprise customers."
        ),
        broader_group="operating_company",
        aliases=(
            "b2b software company",
            "saas company",
            "enterprise SaaS",
            "企业软件公司",
            "企业级软件公司",
            "ToB软件公司",
            "B2B软件公司",
            "SaaS公司",
        ),
        example_signal_domains=(
            "enterprise AI implementation",
            "software market adoption",
            "go-to-market partnerships",
        ),
        ontology_version=ENTITY_TYPE_ONTOLOGY_VERSION,
    ),
    EntityTypeDefinition(
        code="industrial_technology_company",
        display_name="Industrial Technology Company",
        definition=(
            "A company applying technology to industrial, manufacturing, "
            "energy, logistics, or physical-infrastructure markets."
        ),
        broader_group="operating_company",
        aliases=(
            "industrial tech company",
            "hard tech company",
            "deep tech company",
            "产业科技公司",
            "工业科技公司",
            "硬科技公司",
            "深科技公司",
        ),
        example_signal_domains=(
            "industrial AI adoption",
            "new energy technology",
            "advanced manufacturing trends",
        ),
        ontology_version=ENTITY_TYPE_ONTOLOGY_VERSION,
    ),
    EntityTypeDefinition(
        code="growth_stage_company",
        display_name="Growth-Stage Company",
        definition=(
            "A scaling operating company that has moved beyond early product "
            "validation and is expanding teams, markets, or business lines."
        ),
        broader_group="operating_company",
        aliases=(
            "scaleup",
            "growth company",
            "late-stage startup",
            "成长期公司",
            "成长阶段公司",
            "扩张期公司",
            "成长期创业公司",
        ),
        example_signal_domains=(
            "team expansion",
            "new market entry",
            "strategy and operations hiring signals",
        ),
        ontology_version=ENTITY_TYPE_ONTOLOGY_VERSION,
    ),
    EntityTypeDefinition(
        code="venture_capital_firm",
        display_name="Venture Capital Firm",
        definition=(
            "A professional investment firm deploying venture capital into "
            "startups and growth companies."
        ),
        broader_group="capital_provider",
        aliases=(
            "VC",
            "VC firm",
            "venture fund",
            "venture capital fund",
            "风险投资机构",
            "创投机构",
            "VC机构",
            "风险投资基金",
        ),
        example_signal_domains=(
            "fundraising announcements",
            "investment theses",
            "portfolio activity",
        ),
        ontology_version=ENTITY_TYPE_ONTOLOGY_VERSION,
    ),
    EntityTypeDefinition(
        code="corporate_venture_capital",
        display_name="Corporate Venture Capital",
        definition=(
            "A corporate investment unit making strategic venture investments "
            "on behalf of an operating company."
        ),
        broader_group="capital_provider",
        aliases=(
            "CVC",
            "corporate VC",
            "corporate venture arm",
            "企业风险投资",
            "企业创投",
            "企业风险投资部门",
            "CVC机构",
        ),
        example_signal_domains=(
            "strategic venture investment",
            "AI partnership investment",
            "corporate innovation signals",
        ),
        ontology_version=ENTITY_TYPE_ONTOLOGY_VERSION,
    ),
    EntityTypeDefinition(
        code="private_equity_or_growth_equity_firm",
        display_name="Private Equity or Growth Equity Firm",
        definition=(
            "An investment firm deploying private equity or growth equity "
            "capital into established or scaling companies."
        ),
        broader_group="capital_provider",
        aliases=(
            "PE firm",
            "growth equity firm",
            "private equity fund",
            "private equity growth equity firm",
            "私募股权机构",
            "PE机构",
            "成长股权基金",
            "成长股权投资机构",
        ),
        example_signal_domains=(
            "new fund close",
            "technology buyout activity",
            "portfolio operations hiring",
        ),
        ontology_version=ENTITY_TYPE_ONTOLOGY_VERSION,
    ),
    EntityTypeDefinition(
        code="portfolio_company",
        display_name="Portfolio Company",
        definition=(
            "An operating company backed by a venture capital, corporate "
            "venture, private equity, or growth equity investor."
        ),
        broader_group="operating_company",
        aliases=(
            "investor-backed company",
            "venture-backed company",
            "PE-backed company",
            "被投企业",
            "被投公司",
            "投资组合公司",
            "VC被投企业",
            "PE被投企业",
        ),
        example_signal_domains=(
            "venture-backed growth",
            "strategic operations expansion",
            "product strategy signals",
        ),
        ontology_version=ENTITY_TYPE_ONTOLOGY_VERSION,
    ),
    EntityTypeDefinition(
        code="startup_accelerator",
        display_name="Startup Accelerator",
        definition=(
            "An organization running structured programs that support early "
            "stage startups through mentorship, capital, or network access."
        ),
        broader_group="capital_provider",
        aliases=(
            "accelerator",
            "startup program",
            "venture accelerator",
            "创业加速器",
            "初创企业加速器",
            "创业加速项目",
        ),
        example_signal_domains=(
            "startup cohorts",
            "fellowship programs",
            "early-stage ecosystem trends",
        ),
        ontology_version=ENTITY_TYPE_ONTOLOGY_VERSION,
    ),
    EntityTypeDefinition(
        code="venture_studio",
        display_name="Venture Studio",
        definition=(
            "An organization that repeatedly builds, launches, or incubates "
            "new startups using shared capital, teams, and infrastructure."
        ),
        broader_group="capital_provider",
        aliases=(
            "startup studio",
            "company builder",
            "venture builder",
            "创业工作室",
            "企业孵化工作室",
            "创业公司工厂",
        ),
        example_signal_domains=(
            "new startup formation",
            "operator roles",
            "venture-building programs",
        ),
        ontology_version=ENTITY_TYPE_ONTOLOGY_VERSION,
    ),
    EntityTypeDefinition(
        code="management_consulting_firm",
        display_name="Management Consulting Firm",
        definition=(
            "A professional services firm advising organizations on strategy, "
            "operations, markets, transformation, or organizational change."
        ),
        broader_group="professional_services",
        aliases=(
            "strategy consulting firm",
            "management consultancy",
            "consulting firm",
            "管理咨询公司",
            "战略咨询公司",
            "管理咨询机构",
        ),
        example_signal_domains=(
            "practice development",
            "thought leadership",
            "analyst program signals",
        ),
        ontology_version=ENTITY_TYPE_ONTOLOGY_VERSION,
    ),
    EntityTypeDefinition(
        code="technology_consulting_firm",
        display_name="Technology Consulting Firm",
        definition=(
            "A professional services firm helping organizations implement, "
            "integrate, or govern technology and AI systems."
        ),
        broader_group="professional_services",
        aliases=(
            "digital transformation consultancy",
            "AI consulting firm",
            "technology consultancy",
            "技术咨询公司",
            "科技咨询公司",
            "数字化转型咨询公司",
            "AI咨询公司",
        ),
        example_signal_domains=(
            "AI implementation practice growth",
            "enterprise transformation programs",
            "technology partnership announcements",
        ),
        ontology_version=ENTITY_TYPE_ONTOLOGY_VERSION,
    ),
    EntityTypeDefinition(
        code="boutique_investment_bank_or_fa",
        display_name="Boutique Investment Bank or FA",
        definition=(
            "A specialized financial advisory or boutique investment banking "
            "firm advising on capital raising, mergers, acquisitions, or deals."
        ),
        broader_group="professional_services",
        aliases=(
            "FA",
            "financial advisory boutique",
            "boutique investment bank",
            "精品投行",
            "财务顾问机构",
            "融资顾问机构",
            "FA机构",
            "精品财务顾问",
        ),
        example_signal_domains=(
            "deal advisory activity",
            "analyst career paths",
            "FA-to-VC transition signals",
        ),
        ontology_version=ENTITY_TYPE_ONTOLOGY_VERSION,
    ),
    EntityTypeDefinition(
        code="research_institute",
        display_name="Research Institute",
        definition=(
            "An organization producing structured research, analysis, data, "
            "or policy work about industries, technologies, or markets."
        ),
        broader_group="knowledge_institution",
        aliases=(
            "research organization",
            "industry research institution",
            "market research institute",
            "研究机构",
            "产业研究机构",
            "市场研究机构",
            "行业研究院",
        ),
        example_signal_domains=(
            "industry reports",
            "technology adoption research",
            "analyst career patterns",
        ),
        ontology_version=ENTITY_TYPE_ONTOLOGY_VERSION,
    ),
    EntityTypeDefinition(
        code="policy_think_tank",
        display_name="Policy Think Tank",
        definition=(
            "A research-oriented institution producing policy analysis, "
            "recommendations, or public-interest research."
        ),
        broader_group="knowledge_institution",
        aliases=(
            "think tank",
            "policy research institution",
            "public policy institute",
            "政策智库",
            "智库",
            "公共政策研究机构",
            "政策研究院",
        ),
        example_signal_domains=(
            "technology policy analysis",
            "AI governance research",
            "regulatory commentary",
        ),
        ontology_version=ENTITY_TYPE_ONTOLOGY_VERSION,
    ),
    EntityTypeDefinition(
        code="industry_association",
        display_name="Industry Association",
        definition=(
            "A membership or trade organization representing companies, "
            "professionals, or institutions in a sector."
        ),
        broader_group="knowledge_institution",
        aliases=(
            "trade association",
            "industry body",
            "professional association",
            "行业协会",
            "产业协会",
            "商会",
            "专业协会",
        ),
        example_signal_domains=(
            "sector surveys",
            "industry adoption signals",
            "professional standards",
        ),
        ontology_version=ENTITY_TYPE_ONTOLOGY_VERSION,
    ),
    EntityTypeDefinition(
        code="government_or_regulatory_body",
        display_name="Government or Regulatory Body",
        definition=(
            "A public-sector organization that creates, enforces, or signals "
            "policy, regulation, funding programs, or market rules."
        ),
        broader_group="public_sector",
        aliases=(
            "regulator",
            "government agency",
            "public-sector body",
            "政府部门",
            "监管机构",
            "政府机构",
            "行业主管部门",
        ),
        example_signal_domains=(
            "AI regulation",
            "investment policy",
            "public innovation funding",
        ),
        ontology_version=ENTITY_TYPE_ONTOLOGY_VERSION,
    ),
    EntityTypeDefinition(
        code="professional_media",
        display_name="Professional Media",
        definition=(
            "A publication, newsletter, podcast, or editorial organization "
            "covering professional markets, industries, careers, or investment."
        ),
        broader_group="information_platform",
        aliases=(
            "trade media",
            "industry publication",
            "professional publication",
            "专业媒体",
            "行业媒体",
            "产业媒体",
            "财经媒体",
        ),
        example_signal_domains=(
            "funding news",
            "career interviews",
            "industry analysis",
        ),
        ontology_version=ENTITY_TYPE_ONTOLOGY_VERSION,
    ),
    EntityTypeDefinition(
        code="investment_data_provider",
        display_name="Investment Data Provider",
        definition=(
            "A data or analytics platform that tracks investment activity, "
            "funding rounds, transactions, or private-market trends."
        ),
        broader_group="information_platform",
        aliases=(
            "funding data provider",
            "private market data platform",
            "investment intelligence platform",
            "投资数据平台",
            "投融资数据平台",
            "私募市场数据平台",
            "投资情报平台",
        ),
        example_signal_domains=(
            "funding trend data",
            "deal activity",
            "investor market maps",
        ),
        ontology_version=ENTITY_TYPE_ONTOLOGY_VERSION,
    ),
    EntityTypeDefinition(
        code="recruiting_platform",
        display_name="Recruiting Platform",
        definition=(
            "A talent-market platform, job board, career portal, or recruiting "
            "service aggregating or publishing role and program signals."
        ),
        broader_group="talent_market",
        aliases=(
            "job platform",
            "career platform",
            "talent platform",
            "招聘平台",
            "求职平台",
            "职业招聘平台",
            "人才招聘平台",
            "招聘求职平台",
        ),
        example_signal_domains=(
            "internship programs",
            "fellowship availability",
            "entry-level role patterns",
        ),
        ontology_version=ENTITY_TYPE_ONTOLOGY_VERSION,
    ),
)


def get_entity_type_ontology() -> tuple[EntityTypeDefinition, ...]:
    return ENTITY_TYPE_ONTOLOGY


def entity_type_codes() -> tuple[str, ...]:
    return tuple(entity_type.code for entity_type in ENTITY_TYPE_ONTOLOGY)


def entity_type_by_code() -> dict[str, EntityTypeDefinition]:
    return {entity_type.code: entity_type for entity_type in ENTITY_TYPE_ONTOLOGY}


def resolve_entity_type_code(value: str | None) -> str | None:
    code_key = normalize_entity_type_code(value)
    alias_key = normalize_entity_type_alias(value)

    if code_key in PROHIBITED_ENTITY_TYPE_CODES or alias_key in PROHIBITED_ENTITY_TYPE_CODES:
        return None

    aliases = _alias_map()
    return aliases.get(alias_key)


def normalize_entity_type_code(value: str | None) -> str:
    if value is None:
        return ""

    text = unicodedata.normalize("NFKC", str(value)).strip()
    if any(ord(character) > 127 for character in text):
        return ""

    text = text.casefold()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return re.sub(r"_+", "_", text).strip("_")


def normalize_entity_type_alias(value: str | None) -> str:
    if value is None:
        return ""

    text = unicodedata.normalize("NFKC", str(value)).casefold()
    text = text.replace("&", " and ")
    text = re.sub(r"[\u2010-\u2015\-_/\\|]+", " ", text)
    text = re.sub(r"[`'\".,;:!?()[\]{}<>，。；：！？（）【】《》、·•]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", text)
    text = re.sub(r"(?<=[a-z0-9])\s+(?=[\u4e00-\u9fff])", "", text)
    text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[a-z0-9])", "", text)
    return text


def validate_entity_type_ontology(
    ontology: tuple[EntityTypeDefinition, ...],
) -> tuple[EntityTypeDefinition, ...]:
    codes = tuple(entity_type.code for entity_type in ontology)

    if codes != entity_type_codes():
        raise ValueError(
            "Entity Type Ontology must contain exactly the controlled codes."
        )

    for entity_type in ontology:
        if (
            entity_type.ontology_version != ENTITY_TYPE_ONTOLOGY_VERSION
            or not entity_type.definition.strip()
            or not entity_type.display_name.strip()
            or not entity_type.broader_group.strip()
        ):
            raise ValueError("Entity Type Ontology contains an invalid definition.")

    _build_alias_map(ontology)

    return ontology


def _alias_map() -> dict[str, str]:
    return _build_alias_map(ENTITY_TYPE_ONTOLOGY)


def _build_alias_map(
    ontology: tuple[EntityTypeDefinition, ...],
) -> dict[str, str]:
    mapping: dict[str, str] = {}
    owners: dict[str, set[str]] = defaultdict(set)
    raw_values: dict[str, set[str]] = defaultdict(set)

    for entity_type in ontology:
        values = (entity_type.code, entity_type.display_name, *entity_type.aliases)

        for value in values:
            key = normalize_entity_type_alias(value)
            if not key:
                raise ValueError(
                    f"Entity Type Ontology alias for {entity_type.code} is empty."
                )

            owners[key].add(entity_type.code)
            raw_values[key].add(str(value))

            if key not in mapping:
                mapping[key] = entity_type.code

    collisions = {
        key: sorted(codes)
        for key, codes in owners.items()
        if len(codes) > 1
    }
    if collisions:
        details = "; ".join(
            f"{key!r} claimed by {', '.join(codes)} via "
            f"{', '.join(sorted(raw_values[key]))}"
            for key, codes in sorted(collisions.items())
        )
        raise ValueError(f"Duplicate Entity Type aliases are prohibited: {details}")

    return mapping
