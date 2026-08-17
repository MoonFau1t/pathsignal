import json
import re
from typing import Any

from openai import OpenAI

from src.config import (
    ENTITY_DISCOVERY_MAX_PLANS,
    ENTITY_DISCOVERY_MAX_QUERIES_PER_TYPE,
    ENTITY_DISCOVERY_PLANNING_MODEL,
    ENTITY_DISCOVERY_PLANNING_TEMPERATURE,
    ENTITY_DISCOVERY_REGIONS,
    ENTITY_DISCOVERY_RESULTS_PER_PLAN,
    ENTITY_DISCOVERY_LANGUAGES,
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_PROVIDER,
)
from src.models import TargetCareerPath
from src.source_monitoring.entity_discovery_models import (
    ENTITY_DISCOVERY_PLANNING_PROMPT_VERSION,
    EntityDiscoveryPlan,
    EntityDiscoveryQuery,
)
from src.source_monitoring.entity_identity import (
    build_entity_discovery_plan_id,
    build_entity_discovery_query_id,
)
from src.source_monitoring.entity_type_ontology import (
    get_entity_type_ontology,
    resolve_entity_type_code,
)
from src.source_monitoring.models import EntityTypeCandidate, InformationNeed
from src.source_monitoring.prompts import build_entity_discovery_planning_prompt


class EntityDiscoveryPlanningError(Exception):
    """
    Raised when Phase 2 cannot use the DeepSeek planning response.
    """


class EntityDiscoveryPlanningClient:
    """
    Narrow OpenAI-compatible client for DeepSeek Phase 2 plan generation.
    """

    def __init__(
        self,
        provider: str,
        api_key: str,
        base_url: str,
        model: str,
        temperature: float = ENTITY_DISCOVERY_PLANNING_TEMPERATURE,
    ) -> None:
        self.provider = provider
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.temperature = temperature

        if not self.api_key or self.api_key.startswith("your_"):
            raise EntityDiscoveryPlanningError(
                "LLM_API_KEY is missing. Add a real DeepSeek-compatible key "
                "before running Entity Discovery planning."
            )

        self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)

    def generate(
        self,
        *,
        entity_type_candidates: tuple[EntityTypeCandidate, ...],
        information_needs: tuple[InformationNeed, ...],
        target_career_paths: list[TargetCareerPath],
        user_preferences: dict[str, Any],
        entity_type_ontology,
        languages: tuple[str, ...],
        regions: tuple[str, ...],
        max_queries_per_type: int,
        max_total_plans: int,
    ) -> dict[str, Any]:
        prompt = build_entity_discovery_planning_prompt(
            entity_type_candidates=entity_type_candidates,
            information_needs=information_needs,
            target_career_paths=target_career_paths,
            user_preferences=user_preferences,
            entity_type_ontology=entity_type_ontology,
            languages=languages,
            regions=regions,
            max_queries_per_type=max_queries_per_type,
            max_total_plans=max_total_plans,
        )

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You generate Source Monitoring Phase 2 concrete "
                        "entity-discovery query proposals. Return only valid "
                        "JSON. Do not search the web. Do not generate IDs, "
                        "approved sources, RSS feeds, jobs, or Opportunity "
                        "Search plans."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=self.temperature,
            stream=False,
        )

        response_text = _extract_llm_response_text(response)
        if response_text is None or not response_text.strip():
            raise EntityDiscoveryPlanningError(
                "DeepSeek returned an empty EntityDiscoveryPlanning response."
            )

        json_text = _normalize_json_response_text(response_text)
        try:
            parsed = json.loads(json_text)
        except json.JSONDecodeError as error:
            raise EntityDiscoveryPlanningError(
                "EntityDiscoveryPlanning returned invalid JSON after cleanup."
            ) from error

        if not isinstance(parsed, dict):
            raise EntityDiscoveryPlanningError(
                "EntityDiscoveryPlanning response must be a JSON object."
            )

        return parsed


def parse_entity_discovery_query_proposals(
    parsed: Any,
) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
    if not isinstance(parsed, dict):
        return [], ("EntityDiscoveryPlanning response must be a JSON object.",)

    diagnostics = tuple(
        f"Unexpected top-level field rejected: {key}"
        for key in sorted(set(parsed) - {"entity_discovery_queries"})
    )
    raw_queries = parsed.get("entity_discovery_queries")
    if not isinstance(raw_queries, list):
        return [], diagnostics + (
            "EntityDiscoveryPlanning response must contain "
            "entity_discovery_queries list.",
        )

    proposals: list[dict[str, Any]] = []
    proposal_diagnostics: list[str] = list(diagnostics)
    for index, item in enumerate(raw_queries):
        if isinstance(item, dict):
            proposals.append(item)
        else:
            proposal_diagnostics.append(
                f"Query proposal at index {index} rejected: item must be an object."
            )

    return proposals, tuple(proposal_diagnostics)


def plan_entity_discovery(
    *,
    entity_type_candidates: tuple[EntityTypeCandidate, ...],
    information_needs: tuple[InformationNeed, ...],
    target_career_paths: list[TargetCareerPath],
    user_preferences: dict[str, Any],
    entity_type_ontology=None,
    client: EntityDiscoveryPlanningClient | None = None,
    languages: tuple[str, ...] = ENTITY_DISCOVERY_LANGUAGES,
    regions: tuple[str, ...] = ENTITY_DISCOVERY_REGIONS,
    max_plans: int = ENTITY_DISCOVERY_MAX_PLANS,
    max_results_per_plan: int = ENTITY_DISCOVERY_RESULTS_PER_PLAN,
    max_queries_per_type: int = ENTITY_DISCOVERY_MAX_QUERIES_PER_TYPE,
    model: str | None = None,
    provider: str | None = None,
    temperature: float = ENTITY_DISCOVERY_PLANNING_TEMPERATURE,
) -> tuple[tuple[EntityDiscoveryPlan, ...], tuple[str, ...]]:
    _validate_bilingual_capacity(
        entity_type_candidates=entity_type_candidates,
        languages=languages,
        max_queries_per_type=max_queries_per_type,
        max_plans=max_plans,
    )
    ontology = tuple(entity_type_ontology or get_entity_type_ontology())
    selected_provider = provider or (client.provider if client is not None else LLM_PROVIDER)
    selected_model = model or (
        client.model if client is not None else ENTITY_DISCOVERY_PLANNING_MODEL
    )

    planning_client = client or EntityDiscoveryPlanningClient(
        provider=selected_provider,
        api_key=LLM_API_KEY,
        base_url=LLM_BASE_URL,
        model=selected_model,
        temperature=temperature,
    )
    parsed = planning_client.generate(
        entity_type_candidates=entity_type_candidates,
        information_needs=information_needs,
        target_career_paths=target_career_paths,
        user_preferences=user_preferences,
        entity_type_ontology=ontology,
        languages=languages,
        regions=regions,
        max_queries_per_type=max_queries_per_type,
        max_total_plans=max_plans,
    )
    proposals, parse_diagnostics = parse_entity_discovery_query_proposals(parsed)
    plans, validation_diagnostics = validate_and_build_entity_discovery_plans(
        proposals=proposals,
        entity_type_candidates=entity_type_candidates,
        information_needs=information_needs,
        languages=languages,
        regions=regions,
        max_plans=max_plans,
        max_results_per_plan=max_results_per_plan,
        max_queries_per_type=max_queries_per_type,
    )
    return plans, parse_diagnostics + validation_diagnostics


def validate_and_build_entity_discovery_plans(
    *,
    proposals: list[dict[str, Any]],
    entity_type_candidates: tuple[EntityTypeCandidate, ...],
    information_needs: tuple[InformationNeed, ...],
    languages: tuple[str, ...],
    regions: tuple[str, ...],
    max_plans: int,
    max_results_per_plan: int,
    max_queries_per_type: int,
) -> tuple[tuple[EntityDiscoveryPlan, ...], tuple[str, ...]]:
    _validate_bilingual_capacity(
        entity_type_candidates=entity_type_candidates,
        languages=languages,
        max_queries_per_type=max_queries_per_type,
        max_plans=max_plans,
    )
    candidate_by_code = {
        candidate.entity_type_code: candidate
        for candidate in entity_type_candidates
    }
    valid_need_ids = {need.information_need_id for need in information_needs}
    diagnostics: list[str] = []
    accepted_queries: list[EntityDiscoveryQuery] = []
    seen_query_keys: set[tuple[str, str, str, str]] = set()
    per_candidate_language_counts: dict[tuple[str, str], int] = {}
    allowed_fields = {
        "entity_type_code",
        "query_text",
        "language",
        "region",
        "discovery_intent",
        "related_information_need_ids",
        "rationale",
    }

    for index, proposal in enumerate(proposals):
        errors: list[str] = []
        extra_fields = sorted(set(proposal) - allowed_fields)
        if extra_fields:
            errors.append(f"unsupported fields: {extra_fields}")

        raw_entity_type_code = str(proposal.get("entity_type_code", "")).strip()
        entity_type_code = resolve_entity_type_code(raw_entity_type_code)
        candidate = candidate_by_code.get(entity_type_code or "")
        if entity_type_code is None or candidate is None:
            errors.append("entity_type_code is not a selected Phase 1 candidate")

        query_text = str(proposal.get("query_text", "")).strip()
        language = str(proposal.get("language", "")).strip()
        region = str(proposal.get("region", "")).strip()
        discovery_intent = str(proposal.get("discovery_intent", "")).strip()
        related_need_ids = tuple(
            str(item)
            for item in proposal.get("related_information_need_ids", [])
            if item is not None
        )

        if language not in languages:
            errors.append("language is not configured")
        if region not in regions:
            errors.append("region is not configured")
        if not query_text:
            errors.append("query_text is required")
        if _looks_like_url_or_source(query_text):
            errors.append("query_text must not be a URL, feed, or source syntax")
        if _looks_like_job_query(query_text):
            errors.append("query_text must not primarily search current jobs")
        if not _looks_like_concrete_entity_discovery(query_text, discovery_intent):
            errors.append(
                "query_text must target concrete organizations, institutions, "
                "firms, platforms, or information producers"
            )
        if language and not _query_matches_language(query_text, language):
            errors.append("declared language does not match query_text")
        if not related_need_ids:
            errors.append("related_information_need_ids are required")
        elif any(need_id not in valid_need_ids for need_id in related_need_ids):
            errors.append("related_information_need_ids contain unknown IDs")
        if candidate is not None and related_need_ids:
            supported_related_need_ids = tuple(
                need_id
                for need_id in related_need_ids
                if need_id in candidate.related_information_need_ids
            )
            if not supported_related_need_ids:
                errors.append(
                    "related_information_need_ids are not supported by candidate"
                )
            elif len(supported_related_need_ids) != len(related_need_ids):
                diagnostics.append(
                    "DeepSeek query proposal "
                    f"{index} had unsupported related_information_need_ids "
                    "removed."
                )
                related_need_ids = supported_related_need_ids
        if not discovery_intent:
            errors.append("discovery_intent is required")

        if errors:
            diagnostics.append(
                f"DeepSeek query proposal {index} rejected: {'; '.join(errors)}."
            )
            continue

        query_key = (
            candidate.candidate_id,
            language,
            region,
            _normalize_query_key(query_text),
        )
        if query_key in seen_query_keys:
            diagnostics.append(
                f"DeepSeek query proposal {index} consolidated as duplicate."
            )
            continue
        seen_query_keys.add(query_key)

        per_language_limit = _per_language_query_limit(
            max_queries_per_type=max_queries_per_type,
            language_count=len(languages),
        )
        count_key = (candidate.candidate_id, language)
        current_count = per_candidate_language_counts.get(count_key, 0)
        if current_count >= per_language_limit:
            diagnostics.append(
                "DeepSeek query proposal "
                f"{index} skipped by per-type language query limit."
            )
            continue
        per_candidate_language_counts[count_key] = current_count + 1

        accepted_queries.append(
            EntityDiscoveryQuery(
                query_id=build_entity_discovery_query_id(
                    entity_type_candidate_id=candidate.candidate_id,
                    entity_type_code=candidate.entity_type_code,
                    language=language,
                    region=region,
                    query_text=query_text,
                ),
                query_text=query_text,
                language=language,
                region=region,
                entity_type_code=candidate.entity_type_code,
                related_entity_type_candidate_id=candidate.candidate_id,
                related_information_need_ids=tuple(sorted(set(related_need_ids))),
                discovery_intent=discovery_intent,
                rationale=str(proposal.get("rationale", "")).strip(),
            )
        )

    ordered_queries = tuple(
        sorted(
            accepted_queries,
            key=lambda item: (
                -_candidate_priority(candidate_by_code[item.entity_type_code]),
                item.entity_type_code,
                item.language,
                item.region,
                item.query_text,
            ),
        )
    )

    bounded_queries = ordered_queries[:max_plans]
    if len(ordered_queries) > max_plans:
        diagnostics.append(
            f"EntityDiscovery planning produced {len(ordered_queries)} valid "
            f"queries; max_plans is {max_plans}."
        )

    plans: list[EntityDiscoveryPlan] = []
    for query in bounded_queries:
        candidate = candidate_by_code[query.entity_type_code]
        max_results, notes = _budget_for_candidate(
            candidate=candidate,
            max_results_per_plan=max_results_per_plan,
        )
        plan_id = build_entity_discovery_plan_id(
            query_id=query.query_id,
            entity_type_candidate_id=candidate.candidate_id,
        )
        plans.append(
            EntityDiscoveryPlan(
                plan_id=plan_id,
                entity_type_candidate_id=candidate.candidate_id,
                entity_type_code=candidate.entity_type_code,
                queries=(query,),
                language=query.language,
                region=query.region,
                max_results=max_results,
                priority=_candidate_priority(candidate),
                confidence=candidate.confidence,
                planning_notes=notes,
            )
        )

    covered_candidate_ids = {plan.entity_type_candidate_id for plan in plans}
    for candidate in entity_type_candidates:
        if candidate.candidate_id not in covered_candidate_ids:
            diagnostics.append(
                "EntityTypeCandidate not covered by validated DeepSeek plans: "
                f"{candidate.candidate_id} ({candidate.entity_type_code})."
            )
            continue

        for language in languages:
            if not any(
                plan.entity_type_candidate_id == candidate.candidate_id
                and plan.language == language
                for plan in plans
            ):
                diagnostics.append(
                    "EntityTypeCandidate missing validated "
                    f"{language} EntityDiscoveryPlan: {candidate.candidate_id} "
                    f"({candidate.entity_type_code})."
                )

    return tuple(sorted(plans, key=lambda item: item.plan_id)), tuple(diagnostics)


def _validate_bilingual_capacity(
    *,
    entity_type_candidates: tuple[EntityTypeCandidate, ...],
    languages: tuple[str, ...],
    max_queries_per_type: int,
    max_plans: int,
) -> None:
    language_count = len(tuple(dict.fromkeys(languages)))
    candidate_count = len(entity_type_candidates)
    minimum_required_plans = candidate_count * language_count

    if max_queries_per_type < language_count:
        raise EntityDiscoveryPlanningError(
            "max_queries_per_type cannot satisfy configured language coverage: "
            f"{max_queries_per_type} < {language_count}."
        )

    if max_plans < minimum_required_plans:
        raise EntityDiscoveryPlanningError(
            "max_plans cannot satisfy configured language coverage: "
            f"{max_plans} < {minimum_required_plans}."
        )


def _per_language_query_limit(
    *,
    max_queries_per_type: int,
    language_count: int,
) -> int:
    return max(1, max_queries_per_type // max(1, language_count))


def _candidate_priority(candidate: EntityTypeCandidate) -> float:
    support_factor = min(1.0, len(candidate.related_information_need_ids) / 3)
    return round((candidate.confidence * 0.7) + (support_factor * 0.3), 4)


def _budget_for_candidate(
    *,
    candidate: EntityTypeCandidate,
    max_results_per_plan: int,
) -> tuple[int, tuple[str, ...]]:
    if candidate.confidence < 0.6:
        return max(1, round(max_results_per_plan / 2)), (
            "Low-confidence Phase 1 candidate receives lower execution priority, "
            "smaller result budget, and stricter review.",
        )

    return max(1, max_results_per_plan), ()


def _query_matches_language(query_text: str, language: str) -> bool:
    has_cjk = any("\u4e00" <= char <= "\u9fff" for char in query_text)
    has_latin = any("a" <= char.lower() <= "z" for char in query_text)

    if language == "zh":
        return has_cjk
    if language == "en":
        return has_latin and not has_cjk

    return True


def _looks_like_job_query(value: str) -> bool:
    return re.search(
        r"\b(job|jobs|hiring|openings|apply|internship|campus recruiting)\b|"
        r"招聘岗位|校招职位|立即申请|在招职位|职位申请",
        value,
        flags=re.IGNORECASE,
    ) is not None


def _looks_like_url_or_source(value: str) -> bool:
    return re.search(
        r"https?://|www\.|\b(rss|atom feed|feed url)\b|"
        r"\b(site:|intitle:|inurl:|filetype:)\b",
        value,
        flags=re.IGNORECASE,
    ) is not None


def _looks_like_concrete_entity_discovery(
    query_text: str,
    discovery_intent: str,
) -> bool:
    text = f"{query_text} {discovery_intent}".casefold()
    return re.search(
        r"\b(company|companies|firm|firms|organization|organizations|"
        r"institution|institutions|fund|funds|platform|platforms|"
        r"association|associations|provider|providers|official website|"
        r"official site|research center|think tank|media|publisher|"
        r"publishers|publication|publications|agency|agencies|"
        r"accelerator|accelerators|studio|studios|directory|list)\b|"
        r"公司|企业|机构|平台|协会|基金|官网|官方网站|智库|研究中心|媒体|"
        r"数据服务商|名录|加速器|工作室|监管部门|出版物",
        text,
        flags=re.IGNORECASE,
    ) is not None


def _normalize_query_key(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().casefold())


def _extract_llm_response_text(response: Any) -> str | None:
    choices = getattr(response, "choices", None)
    if not choices:
        return None

    message = getattr(choices[0], "message", None)
    if message is None:
        return None

    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            item.get("text", "")
            for item in content
            if isinstance(item, dict)
        )

    return None


def _normalize_json_response_text(response_text: str) -> str:
    stripped_text = response_text.strip()
    fenced_match = re.search(
        r"```(?:json)?\s*(.*?)\s*```",
        stripped_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if fenced_match is not None:
        stripped_text = fenced_match.group(1).strip()
    return stripped_text
