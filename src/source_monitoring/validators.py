import re
from collections import defaultdict
from typing import Any

from src.models import TargetCareerPath
from src.source_monitoring.entity_type_ontology import (
    ENTITY_TYPE_ONTOLOGY_VERSION,
    PROHIBITED_ENTITY_TYPE_CODES,
    resolve_entity_type_code,
    normalize_entity_type_code,
)
from src.source_monitoring.identity import (
    build_entity_type_candidate_id,
    build_information_need_id,
)
from src.source_monitoring.models import (
    ENTITY_TYPE_CANDIDATE_SCHEMA_VERSION,
    EntityTypeCandidate,
    EntityTypeDefinition,
    INFORMATION_NEED_SCHEMA_VERSION,
    InformationNeed,
    InformationNeedPriority,
    LLMExecutionMetadata,
    MonitoringObjectiveCode,
    ProposedEntityType,
    RejectedEntityTypeSuggestion,
    RejectedInformationNeedSuggestion,
)


ALLOWED_INFORMATION_NEED_FIELDS = {
    "need_key",
    "objective_code",
    "title",
    "description",
    "related_target_career_path_ids",
    "signal_examples",
    "rationale",
    "priority",
    "confidence",
}

MAX_TITLE_LENGTH = 120
MAX_DESCRIPTION_LENGTH = 800
MAX_RATIONALE_LENGTH = 800
MAX_SIGNAL_EXAMPLE_LENGTH = 220

_NEED_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,79}$")
_URL_PATTERN = re.compile(r"https?://|www\.", re.IGNORECASE)
_DOMAIN_PATTERN = re.compile(
    r"\b[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\."
    r"(?:com|org|net|io|ai|co|cn|edu|gov|rss|xml)\b",
    re.IGNORECASE,
)
_CJK_DOMAIN_PATTERN = re.compile(
    r"[\u4e00-\u9fff0-9-]+\.(?:cn|com|net|org|中国|公司|网络)\b",
    re.IGNORECASE,
)
_RSS_PATTERN = re.compile(r"\b(rss|feed url|atom feed|xml feed)\b", re.IGNORECASE)
_SEARCH_PATTERN = re.compile(r"\b(site:|intitle:|inurl:|filetype:)\b", re.IGNORECASE)
_SEARCH_PLAN_PATTERN = re.compile(
    r"\b(searchplan|search plan|search api query|search query)\b",
    re.IGNORECASE,
)
_JOB_POSTING_PATTERN = re.compile(
    r"\b(job posting|job listing|apply now|job id|opening at)\b",
    re.IGNORECASE,
)
_ENTITY_TYPE_PATTERN = re.compile(
    r"\b(entity type|entity types|companies, investors|organizations, funds)\b",
    re.IGNORECASE,
)
_ARTICLE_PATTERN = re.compile(
    r"\barticle title\b|\bheadline\s*:|\bpress release titled\b",
    re.IGNORECASE,
)
_CONCRETE_ORG_NAME_PATTERN = re.compile(
    r"\b(OpenAI|Google|Microsoft|Amazon|Meta|Tencent|Alibaba|ByteDance)\b",
    re.IGNORECASE,
)
_CONCRETE_CJK_ORG_NAME_PATTERN = re.compile(
    r"腾讯|阿里巴巴|字节跳动|百度|华为|小米|红杉资本|高瓴|经纬创投|麦肯锡|"
    r"波士顿咨询|贝恩|德勤|埃森哲|微软|谷歌|亚马逊"
)

ALLOWED_ENTITY_TYPE_CANDIDATE_FIELDS = {
    "entity_type_code",
    "related_information_need_ids",
    "rationale",
    "discovery_terms",
    "confidence",
}

ALLOWED_PROPOSED_ENTITY_TYPE_FIELDS = {
    "proposed_code",
    "display_name",
    "definition",
    "broader_group",
    "supporting_information_need_ids",
    "closest_canonical_type_codes",
    "why_canonical_types_are_insufficient",
    "rationale",
    "confidence",
}

MAX_ENTITY_TYPE_TEXT_LENGTH = 800
MAX_DISCOVERY_TERM_LENGTH = 160

_ENTITY_TYPE_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,99}$")
_EXECUTABLE_QUERY_PATTERN = re.compile(
    r"\b(site:|intitle:|inurl:|filetype:)\b|\"[^\"]+\"\s+(?:AND|OR)\s+\"?[\w(]",
    re.IGNORECASE,
)
_PHASE1_CONCRETE_ENTITY_PATTERN = re.compile(
    r"\b("
    r"OpenAI|Google|Microsoft|Amazon|Meta|Tencent|Alibaba|ByteDance|"
    r"Sequoia\s+Capital|Andreessen\s+Horowitz|a16z|McKinsey|BCG|Bain|"
    r"Deloitte|Accenture"
    r")\b",
    re.IGNORECASE,
)
_PHASE1_CONCRETE_CJK_ENTITY_PATTERN = re.compile(
    r"腾讯|阿里巴巴|字节跳动|百度|华为|小米|红杉资本|高瓴|经纬创投|麦肯锡|"
    r"波士顿咨询|贝恩|德勤|埃森哲|微软|谷歌|亚马逊"
)


def parse_information_need_suggestions(parsed: Any) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Extract raw suggestions from strict top-level LLM JSON.
    """

    if not isinstance(parsed, dict):
        return [], ["InformationNeed response must be a JSON object."]

    raw_needs = parsed.get("information_needs")
    unexpected_keys = sorted(set(parsed) - {"information_needs"})
    diagnostics = [
        f"Unexpected top-level field rejected: {key}"
        for key in unexpected_keys
    ]

    if not isinstance(raw_needs, list):
        return [], diagnostics + [
            "InformationNeed response must contain information_needs list."
        ]

    suggestions: list[dict[str, Any]] = []

    for index, item in enumerate(raw_needs):
        if isinstance(item, dict):
            suggestions.append(item)
        else:
            diagnostics.append(
                f"Suggestion at index {index} rejected: item must be an object."
            )

    return suggestions, diagnostics


def validate_normalize_and_deduplicate_information_needs(
    *,
    suggestions: list[dict[str, Any]],
    target_career_paths: list[TargetCareerPath],
    llm_metadata: LLMExecutionMetadata,
    input_fingerprint: str,
    max_total: int,
    max_signal_examples: int,
    max_per_path_objective: int,
) -> tuple[
    tuple[InformationNeed, ...],
    tuple[RejectedInformationNeedSuggestion, ...],
    tuple[str, ...],
]:
    """
    Validate LLM suggestions and return normalized, deduplicated needs.
    """

    valid_path_ids = tuple(path.path_id for path in target_career_paths)
    valid_path_id_set = set(valid_path_ids)
    normalized: list[InformationNeed] = []
    rejected: list[RejectedInformationNeedSuggestion] = []
    diagnostics: list[str] = []

    if len(suggestions) > max_total:
        diagnostics.append(
            f"LLM returned {len(suggestions)} suggestions; max_total is {max_total}."
        )

    for index, suggestion in enumerate(suggestions):
        need, errors, item_diagnostics = _validate_one_suggestion(
            suggestion=suggestion,
            fallback_index=index,
            valid_path_id_set=valid_path_id_set,
            llm_metadata=llm_metadata,
            input_fingerprint=input_fingerprint,
            max_signal_examples=max_signal_examples,
        )
        diagnostics.extend(item_diagnostics)

        if errors:
            rejected.append(
                RejectedInformationNeedSuggestion(
                    suggestion=dict(suggestion),
                    reason="; ".join(errors),
                    diagnostics=tuple(item_diagnostics),
                )
            )
            continue

        normalized.append(need)

    deduplicated, dedupe_diagnostics = _deduplicate_needs(normalized)
    diagnostics.extend(dedupe_diagnostics)

    bounded, limit_rejections, limit_diagnostics = _apply_limits(
        deduplicated,
        max_total=max_total,
        max_per_path_objective=max_per_path_objective,
    )
    rejected.extend(limit_rejections)
    diagnostics.extend(limit_diagnostics)

    ordered = tuple(
        sorted(
            bounded,
            key=lambda need: (
                _priority_rank(need.priority),
                need.objective_code.value,
                need.need_key,
                need.information_need_id,
            ),
        )
    )

    return ordered, tuple(rejected), tuple(diagnostics)


def _validate_one_suggestion(
    *,
    suggestion: dict[str, Any],
    fallback_index: int,
    valid_path_id_set: set[str],
    llm_metadata: LLMExecutionMetadata,
    input_fingerprint: str,
    max_signal_examples: int,
) -> tuple[InformationNeed | None, list[str], list[str]]:
    errors: list[str] = []
    diagnostics: list[str] = []

    extra_fields = sorted(set(suggestion) - ALLOWED_INFORMATION_NEED_FIELDS)
    if extra_fields:
        errors.append(f"unsupported fields: {extra_fields}")

    need_key = _normalize_need_key(suggestion.get("need_key"))
    if not _NEED_KEY_PATTERN.match(need_key):
        errors.append("need_key must be snake_case and 3-80 characters")

    objective_code = _normalize_objective_code(suggestion.get("objective_code"))
    if objective_code is None:
        errors.append("objective_code is not in the fixed taxonomy")

    title = _normalize_text(suggestion.get("title"), max_length=MAX_TITLE_LENGTH)
    description = _normalize_text(
        suggestion.get("description"),
        max_length=MAX_DESCRIPTION_LENGTH,
    )
    rationale = _normalize_text(
        suggestion.get("rationale"),
        max_length=MAX_RATIONALE_LENGTH,
    )

    if not title:
        errors.append("title must be non-empty")
    if not description:
        errors.append("description must be non-empty")
    if not rationale:
        errors.append("rationale must be non-empty")

    related_path_ids = _normalize_path_ids(
        suggestion.get("related_target_career_path_ids")
    )
    if not related_path_ids:
        errors.append("related_target_career_path_ids must contain at least one ID")

    unknown_path_ids = [
        path_id for path_id in related_path_ids if path_id not in valid_path_id_set
    ]
    if unknown_path_ids:
        errors.append(f"unknown TargetCareerPath IDs: {unknown_path_ids}")

    priority = _normalize_priority(suggestion.get("priority"))
    if priority is None:
        errors.append("priority must be high, medium, or low")

    confidence = _normalize_confidence(suggestion.get("confidence"))
    if confidence is None:
        errors.append("confidence must be between 0.0 and 1.0")

    signal_examples, example_diagnostics = _normalize_signal_examples(
        suggestion.get("signal_examples"),
        max_signal_examples=max_signal_examples,
    )
    diagnostics.extend(example_diagnostics)
    if not signal_examples:
        errors.append("signal_examples must contain at least one generic example")

    boundary_text = " ".join(
        [need_key, title, description, rationale, " ".join(signal_examples)]
    )
    boundary_errors = _boundary_errors(boundary_text)
    errors.extend(boundary_errors)

    if errors:
        return None, errors, diagnostics

    objective = objective_code or MonitoringObjectiveCode.OPPORTUNITY
    selected_priority = priority or InformationNeedPriority.MEDIUM
    selected_confidence = confidence if confidence is not None else 0.0
    information_need_id = build_information_need_id(
        objective_code=objective.value,
        need_key=need_key,
    )
    provenance = {
        "provider": llm_metadata.provider,
        "model": llm_metadata.model,
        "prompt_version": llm_metadata.prompt_version,
        "schema_version": INFORMATION_NEED_SCHEMA_VERSION,
        "input_fingerprint": input_fingerprint,
        "source_suggestion_index": fallback_index,
    }

    return (
        InformationNeed(
            information_need_id=information_need_id,
            need_key=need_key,
            objective_code=objective,
            title=title,
            description=description,
            related_target_career_path_ids=tuple(related_path_ids),
            signal_examples=tuple(signal_examples),
            rationale=rationale,
            priority=selected_priority,
            confidence=round(selected_confidence, 4),
            provenance=provenance,
        ),
        [],
        diagnostics,
    )


def _deduplicate_needs(
    needs: list[InformationNeed],
) -> tuple[list[InformationNeed], list[str]]:
    groups: dict[tuple[str, str], list[InformationNeed]] = defaultdict(list)
    diagnostics: list[str] = []

    for need in needs:
        groups[(need.objective_code.value, need.need_key)].append(need)

    merged_needs: list[InformationNeed] = []

    for key in sorted(groups):
        group = groups[key]

        if len(group) == 1:
            merged_needs.append(group[0])
            continue

        diagnostics.append(
            "Merged duplicate InformationNeed suggestions for "
            f"{key[0]}:{key[1]}."
        )
        first = sorted(
            group,
            key=lambda need: (
                _priority_rank(need.priority),
                -need.confidence,
                need.title,
            ),
        )[0]
        path_ids = _union_strings(
            *(list(need.related_target_career_path_ids) for need in group)
        )
        examples = _union_strings(
            *(list(need.signal_examples) for need in group)
        )
        rationales = _union_strings(*([need.rationale] for need in group))
        confidence = round(
            sum(need.confidence for need in group) / len(group),
            4,
        )
        priority = min(
            (need.priority for need in group),
            key=_priority_rank,
        )

        merged_needs.append(
            InformationNeed(
                information_need_id=first.information_need_id,
                need_key=first.need_key,
                objective_code=first.objective_code,
                title=first.title,
                description=first.description,
                related_target_career_path_ids=tuple(path_ids),
                signal_examples=tuple(examples),
                rationale=" | ".join(rationales),
                priority=priority,
                confidence=confidence,
                provenance=first.provenance,
                schema_version=first.schema_version,
            )
        )

    return merged_needs, diagnostics


def _apply_limits(
    needs: list[InformationNeed],
    *,
    max_total: int,
    max_per_path_objective: int,
) -> tuple[
    list[InformationNeed],
    list[RejectedInformationNeedSuggestion],
    list[str],
]:
    diagnostics: list[str] = []
    rejected: list[RejectedInformationNeedSuggestion] = []
    kept: list[InformationNeed] = []
    counts_by_path_objective: dict[tuple[str, str], int] = defaultdict(int)

    ordered = sorted(
        needs,
        key=lambda need: (
            _priority_rank(need.priority),
            -need.confidence,
            need.objective_code.value,
            need.need_key,
        ),
    )

    for need in ordered:
        if len(kept) >= max_total:
            diagnostics.append(
                f"Rejected {need.need_key}: max_total {max_total} reached."
            )
            rejected.append(
                RejectedInformationNeedSuggestion(
                    suggestion=need.to_dict(),
                    reason=f"max_total {max_total} reached",
                )
            )
            continue

        exceeds_path_limit = False
        for path_id in need.related_target_career_path_ids:
            key = (path_id, need.objective_code.value)
            if counts_by_path_objective[key] >= max_per_path_objective:
                exceeds_path_limit = True
                break

        if exceeds_path_limit:
            diagnostics.append(
                f"Rejected {need.need_key}: max_per_path_objective "
                f"{max_per_path_objective} reached."
            )
            rejected.append(
                RejectedInformationNeedSuggestion(
                    suggestion=need.to_dict(),
                    reason=(
                        "max_per_path_objective "
                        f"{max_per_path_objective} reached"
                    ),
                )
            )
            continue

        kept.append(need)

        for path_id in need.related_target_career_path_ids:
            counts_by_path_objective[(path_id, need.objective_code.value)] += 1

    return kept, rejected, diagnostics


def _normalize_need_key(value: Any) -> str:
    normalized = _normalize_text(value, max_length=80).lower()
    normalized = re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")
    return normalized


def _normalize_objective_code(value: Any) -> MonitoringObjectiveCode | None:
    normalized = str(value or "").strip().lower()

    try:
        return MonitoringObjectiveCode(normalized)
    except ValueError:
        return None


def _normalize_priority(value: Any) -> InformationNeedPriority | None:
    normalized = str(value or "").strip().lower()

    try:
        return InformationNeedPriority(normalized)
    except ValueError:
        return None


def _normalize_confidence(value: Any) -> float | None:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return None

    if confidence < 0.0 or confidence > 1.0:
        return None

    return confidence


def _normalize_path_ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []

    return _union_strings(
        [
            str(item).strip()
            for item in value
            if item is not None and str(item).strip()
        ]
    )


def _normalize_signal_examples(
    value: Any,
    *,
    max_signal_examples: int,
) -> tuple[list[str], list[str]]:
    if not isinstance(value, list):
        return [], []

    diagnostics: list[str] = []
    examples = _union_strings(
        [
            _normalize_text(item, max_length=MAX_SIGNAL_EXAMPLE_LENGTH)
            for item in value
            if item is not None
        ]
    )

    if len(examples) > max_signal_examples:
        diagnostics.append(
            "Truncated signal_examples from "
            f"{len(examples)} to {max_signal_examples}."
        )
        examples = examples[:max_signal_examples]

    return examples, diagnostics


def _normalize_text(value: Any, *, max_length: int) -> str:
    if value is None:
        return ""

    normalized = re.sub(r"\s+", " ", str(value)).strip()
    normalized = normalized.strip(" \t\r\n.;")

    if len(normalized) > max_length:
        normalized = normalized[:max_length].rstrip()

    return normalized


def _boundary_errors(text: str) -> list[str]:
    errors: list[str] = []

    checks = (
        (_URL_PATTERN, "must not contain URLs"),
        (_DOMAIN_PATTERN, "must not contain domains"),
        (_CJK_DOMAIN_PATTERN, "must not contain domains"),
        (_RSS_PATTERN, "must not contain RSS/feed references"),
        (_SEARCH_PATTERN, "must not contain search query syntax"),
        (_SEARCH_PLAN_PATTERN, "must not contain search plans or queries"),
        (_JOB_POSTING_PATTERN, "must not contain concrete job listings"),
        (_ENTITY_TYPE_PATTERN, "must not contain entity type lists"),
        (_ARTICLE_PATTERN, "must not contain article titles"),
        (
            _CONCRETE_ORG_NAME_PATTERN,
            "must not present concrete organizations as needs",
        ),
        (
            _CONCRETE_CJK_ORG_NAME_PATTERN,
            "must not present concrete organizations as needs",
        ),
    )

    for pattern, message in checks:
        if pattern.search(text):
            errors.append(message)

    return errors


def _union_strings(*groups: list[str]) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []

    for group in groups:
        for item in group:
            normalized = re.sub(r"\s+", " ", str(item)).strip()
            key = normalized.casefold()

            if not normalized or key in seen:
                continue

            seen.add(key)
            merged.append(normalized)

    return merged


def _priority_rank(priority: InformationNeedPriority) -> int:
    return {
        InformationNeedPriority.HIGH: 0,
        InformationNeedPriority.MEDIUM: 1,
        InformationNeedPriority.LOW: 2,
    }[priority]


def parse_entity_type_expansion_suggestions(
    parsed: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """
    Extract raw Phase 1 suggestions from strict top-level LLM JSON.
    """

    if not isinstance(parsed, dict):
        return [], [], ["EntityTypeExpansion response must be a JSON object."]

    unexpected_keys = sorted(
        set(parsed) - {"entity_type_candidates", "proposed_new_types"}
    )
    diagnostics = [
        f"Unexpected top-level field rejected: {key}"
        for key in unexpected_keys
    ]

    raw_candidates = parsed.get("entity_type_candidates", [])
    raw_proposed = parsed.get("proposed_new_types", [])

    if not isinstance(raw_candidates, list):
        diagnostics.append(
            "EntityTypeExpansion response must contain entity_type_candidates list."
        )
        raw_candidates = []

    if not isinstance(raw_proposed, list):
        diagnostics.append(
            "EntityTypeExpansion response must contain proposed_new_types list."
        )
        raw_proposed = []

    candidates: list[dict[str, Any]] = []
    proposed: list[dict[str, Any]] = []

    for index, item in enumerate(raw_candidates):
        if isinstance(item, dict):
            candidates.append(item)
        else:
            diagnostics.append(
                f"EntityTypeCandidate at index {index} rejected: item must be an object."
            )

    for index, item in enumerate(raw_proposed):
        if isinstance(item, dict):
            proposed.append(item)
        else:
            diagnostics.append(
                f"ProposedEntityType at index {index} rejected: item must be an object."
            )

    return candidates, proposed, diagnostics


def validate_normalize_and_deduplicate_entity_type_expansion(
    *,
    candidate_suggestions: list[dict[str, Any]],
    proposed_type_suggestions: list[dict[str, Any]],
    information_needs: tuple[InformationNeed, ...],
    target_career_paths: list[TargetCareerPath],
    ontology: tuple[EntityTypeDefinition, ...],
    llm_metadata: LLMExecutionMetadata,
    input_fingerprint: str,
    max_canonical_candidates: int,
    max_proposed_types: int,
    max_types_per_need: int,
    max_discovery_terms: int,
) -> tuple[
    tuple[EntityTypeCandidate, ...],
    tuple[ProposedEntityType, ...],
    tuple[RejectedEntityTypeSuggestion, ...],
    tuple[str, ...],
    tuple[str, ...],
]:
    """
    Validate, canonicalize, merge, and account for Phase 1 suggestions.

    Unknown but structurally valid candidate type codes are preserved as
    proposed new types instead of being silently mapped to the ontology.
    Duplicate canonical candidates are merged by entity_type_code, with
    confidence averaged deterministically.
    """

    needs_by_id = {
        need.information_need_id: need
        for need in information_needs
    }
    path_order = [path.path_id for path in target_career_paths]
    ontology_by_code = {entity_type.code: entity_type for entity_type in ontology}
    canonical: list[EntityTypeCandidate] = []
    proposed: list[ProposedEntityType] = []
    rejected: list[RejectedEntityTypeSuggestion] = []
    diagnostics: list[str] = []
    types_by_need: dict[str, set[str]] = defaultdict(set)

    for index, suggestion in enumerate(candidate_suggestions):
        candidate, proposed_gap, errors, item_diagnostics = _validate_one_entity_type_candidate(
            suggestion=suggestion,
            fallback_index=index,
            needs_by_id=needs_by_id,
            path_order=path_order,
            ontology_by_code=ontology_by_code,
            llm_metadata=llm_metadata,
            input_fingerprint=input_fingerprint,
            max_discovery_terms=max_discovery_terms,
        )
        diagnostics.extend(item_diagnostics)

        if errors:
            rejected.append(
                RejectedEntityTypeSuggestion(
                    suggestion=dict(suggestion),
                    reason="; ".join(errors),
                    diagnostics=tuple(item_diagnostics),
                    source_suggestion_index=index,
                )
            )
            continue

        if candidate is not None:
            for need_id in candidate.related_information_need_ids:
                types_by_need[need_id].add(candidate.entity_type_code)
            canonical.append(candidate)
        elif proposed_gap is not None:
            proposed.append(proposed_gap)

    for index, suggestion in enumerate(proposed_type_suggestions):
        proposed_type, errors, item_diagnostics = _validate_one_proposed_entity_type(
            suggestion=suggestion,
            fallback_index=index,
            needs_by_id=needs_by_id,
            path_order=path_order,
            llm_metadata=llm_metadata,
            input_fingerprint=input_fingerprint,
        )
        diagnostics.extend(item_diagnostics)

        if errors:
            rejected.append(
                RejectedEntityTypeSuggestion(
                    suggestion=dict(suggestion),
                    reason="; ".join(errors),
                    diagnostics=tuple(item_diagnostics),
                    source_suggestion_index=index,
                )
            )
            continue

        proposed.append(proposed_type)

    limited_canonical_input: list[EntityTypeCandidate] = []
    for candidate in canonical:
        allowed = True
        for need_id in candidate.related_information_need_ids:
            if len(types_by_need[need_id]) > max_types_per_need:
                ordered_codes = sorted(types_by_need[need_id])
                if candidate.entity_type_code not in ordered_codes[:max_types_per_need]:
                    allowed = False
                    break

        if allowed:
            limited_canonical_input.append(candidate)
        else:
            diagnostics.append(
                "Rejected EntityTypeCandidate because max_types_per_need "
                f"{max_types_per_need} was reached."
            )
            rejected.append(
                RejectedEntityTypeSuggestion(
                    suggestion=candidate.to_dict(),
                    reason=f"max_types_per_need {max_types_per_need} reached",
                )
            )

    merged = _deduplicate_entity_type_candidates(
        limited_canonical_input,
        ontology_by_code=ontology_by_code,
        path_order=path_order,
    )

    if len(merged) > max_canonical_candidates:
        diagnostics.append(
            "Canonical EntityTypeCandidates truncated from "
            f"{len(merged)} to {max_canonical_candidates}."
        )
        for candidate in merged[max_canonical_candidates:]:
            rejected.append(
                RejectedEntityTypeSuggestion(
                    suggestion=candidate.to_dict(),
                    reason=f"max_canonical_candidates {max_canonical_candidates} reached",
                )
            )
        merged = merged[:max_canonical_candidates]

    ordered_proposed = tuple(
        sorted(
            proposed[:max_proposed_types],
            key=lambda item: item.proposed_code,
        )
    )
    if len(proposed) > max_proposed_types:
        diagnostics.append(
            "ProposedEntityTypes truncated from "
            f"{len(proposed)} to {max_proposed_types}."
        )
        for proposed_type in proposed[max_proposed_types:]:
            rejected.append(
                RejectedEntityTypeSuggestion(
                    suggestion=proposed_type.to_dict(),
                    reason=f"max_proposed_types {max_proposed_types} reached",
                )
            )

    covered_need_ids = {
        need_id
        for candidate in merged
        for need_id in candidate.related_information_need_ids
    } | {
        need_id
        for proposed_type in ordered_proposed
        for need_id in proposed_type.supporting_information_need_ids
    }
    uncovered = tuple(
        need.information_need_id
        for need in sorted(information_needs, key=lambda item: item.information_need_id)
        if need.information_need_id not in covered_need_ids
    )

    for need_id in uncovered:
        diagnostics.append(
            f"InformationNeed {need_id} is not covered by a canonical or proposed type."
        )

    return (
        tuple(merged),
        ordered_proposed,
        tuple(
            sorted(
                rejected,
                key=lambda item: (
                    item.source_suggestion_index if item.source_suggestion_index is not None else 999999,
                    item.reason,
                ),
            )
        ),
        uncovered,
        tuple(diagnostics),
    )


def _validate_one_entity_type_candidate(
    *,
    suggestion: dict[str, Any],
    fallback_index: int,
    needs_by_id: dict[str, InformationNeed],
    path_order: list[str],
    ontology_by_code: dict[str, EntityTypeDefinition],
    llm_metadata: LLMExecutionMetadata,
    input_fingerprint: str,
    max_discovery_terms: int,
) -> tuple[
    EntityTypeCandidate | None,
    ProposedEntityType | None,
    list[str],
    list[str],
]:
    errors: list[str] = []
    diagnostics: list[str] = []

    extra_fields = sorted(set(suggestion) - ALLOWED_ENTITY_TYPE_CANDIDATE_FIELDS)
    if extra_fields:
        errors.append(f"unsupported fields: {extra_fields}")

    raw_code = suggestion.get("entity_type_code")
    normalized_code = normalize_entity_type_code(raw_code)
    resolved_code = resolve_entity_type_code(str(raw_code or ""))

    if normalized_code in PROHIBITED_ENTITY_TYPE_CODES:
        errors.append("SourceType acquisition methods are not entity types")

    if resolved_code is None and not _ENTITY_TYPE_CODE_PATTERN.match(normalized_code):
        errors.append("entity_type_code must be snake_case and 3-100 characters")

    info_need_ids = _normalize_information_need_ids(
        suggestion.get("related_information_need_ids")
    )
    if not info_need_ids:
        errors.append("related_information_need_ids must contain at least one ID")

    unknown_need_ids = [
        need_id for need_id in info_need_ids if need_id not in needs_by_id
    ]
    if unknown_need_ids:
        errors.append(f"unknown InformationNeed IDs: {unknown_need_ids}")

    rationale = _normalize_text(
        suggestion.get("rationale"),
        max_length=MAX_ENTITY_TYPE_TEXT_LENGTH,
    )
    if not rationale:
        errors.append("rationale must be non-empty")

    discovery_terms, term_diagnostics = _normalize_discovery_terms(
        suggestion.get("discovery_terms"),
        max_discovery_terms=max_discovery_terms,
    )
    diagnostics.extend(term_diagnostics)
    if not discovery_terms:
        errors.append("discovery_terms must contain at least one generic term")

    confidence = _normalize_confidence(suggestion.get("confidence"))
    if confidence is None:
        errors.append("confidence must be between 0.0 and 1.0")

    boundary_text = " ".join(
        [
            str(raw_code or ""),
            rationale,
            " ".join(discovery_terms),
        ]
    )
    errors.extend(_entity_type_boundary_errors(boundary_text))

    if errors:
        return None, None, errors, diagnostics

    if resolved_code is None:
        proposed = _candidate_gap_to_proposed_type(
            normalized_code=normalized_code,
            suggestion=suggestion,
            information_need_ids=info_need_ids,
            needs_by_id=needs_by_id,
            path_order=path_order,
            llm_metadata=llm_metadata,
            input_fingerprint=input_fingerprint,
            fallback_index=fallback_index,
            confidence=confidence if confidence is not None else 0.0,
            rationale=rationale,
        )
        return None, proposed, [], diagnostics

    definition = ontology_by_code[resolved_code]
    path_ids = _derive_path_ids(info_need_ids, needs_by_id, path_order)
    objective_codes = _derive_objective_codes(info_need_ids, needs_by_id)
    provenance = _entity_type_provenance(
        llm_metadata=llm_metadata,
        input_fingerprint=input_fingerprint,
        source_suggestion_index=fallback_index,
    )

    return (
        EntityTypeCandidate(
            candidate_id=build_entity_type_candidate_id(
                entity_type_code=resolved_code,
                ontology_version=definition.ontology_version,
            ),
            entity_type_code=resolved_code,
            display_name=definition.display_name,
            related_information_need_ids=tuple(info_need_ids),
            related_target_career_path_ids=tuple(path_ids),
            supported_monitoring_objectives=tuple(objective_codes),
            rationale=rationale,
            discovery_terms=tuple(discovery_terms),
            confidence=round(confidence if confidence is not None else 0.0, 4),
            provenance=provenance,
        ),
        None,
        [],
        diagnostics,
    )


def _validate_one_proposed_entity_type(
    *,
    suggestion: dict[str, Any],
    fallback_index: int,
    needs_by_id: dict[str, InformationNeed],
    path_order: list[str],
    llm_metadata: LLMExecutionMetadata,
    input_fingerprint: str,
) -> tuple[ProposedEntityType | None, list[str], list[str]]:
    errors: list[str] = []
    diagnostics: list[str] = []

    extra_fields = sorted(set(suggestion) - ALLOWED_PROPOSED_ENTITY_TYPE_FIELDS)
    if extra_fields:
        errors.append(f"unsupported fields: {extra_fields}")

    proposed_code = normalize_entity_type_code(suggestion.get("proposed_code"))
    if not _ENTITY_TYPE_CODE_PATTERN.match(proposed_code):
        errors.append("proposed_code must be snake_case and 3-100 characters")

    if resolve_entity_type_code(proposed_code) is not None:
        errors.append("proposed type duplicates a canonical ontology type")

    display_name = _normalize_text(suggestion.get("display_name"), max_length=120)
    definition = _normalize_text(
        suggestion.get("definition"),
        max_length=MAX_ENTITY_TYPE_TEXT_LENGTH,
    )
    broader_group = normalize_entity_type_code(suggestion.get("broader_group"))
    insufficiency = _normalize_text(
        suggestion.get("why_canonical_types_are_insufficient"),
        max_length=MAX_ENTITY_TYPE_TEXT_LENGTH,
    )
    rationale = _normalize_text(
        suggestion.get("rationale"),
        max_length=MAX_ENTITY_TYPE_TEXT_LENGTH,
    )

    if not display_name:
        errors.append("display_name must be non-empty")
    if not definition:
        errors.append("definition must be non-empty")
    if not broader_group:
        errors.append("broader_group must be non-empty")
    if not insufficiency:
        errors.append("why_canonical_types_are_insufficient must be non-empty")
    if not rationale:
        errors.append("rationale must be non-empty")

    info_need_ids = _normalize_information_need_ids(
        suggestion.get("supporting_information_need_ids")
    )
    if not info_need_ids:
        errors.append("supporting_information_need_ids must contain at least one ID")

    unknown_need_ids = [
        need_id for need_id in info_need_ids if need_id not in needs_by_id
    ]
    if unknown_need_ids:
        errors.append(f"unknown InformationNeed IDs: {unknown_need_ids}")

    closest_codes = _normalize_closest_canonical_codes(
        suggestion.get("closest_canonical_type_codes")
    )
    if closest_codes is None:
        errors.append("closest_canonical_type_codes contain unknown ontology codes")
        closest_codes = []

    confidence = _normalize_confidence(suggestion.get("confidence"))
    if confidence is None:
        errors.append("confidence must be between 0.0 and 1.0")

    errors.extend(
        _entity_type_boundary_errors(
            " ".join(
                [
                    proposed_code,
                    display_name,
                    definition,
                    broader_group,
                    insufficiency,
                    rationale,
                    " ".join(closest_codes),
                ]
            )
        )
    )

    if errors:
        return None, errors, diagnostics

    return (
        ProposedEntityType(
            proposed_code=proposed_code,
            display_name=display_name,
            definition=definition,
            broader_group=broader_group,
            supporting_information_need_ids=tuple(info_need_ids),
            related_target_career_path_ids=tuple(
                _derive_path_ids(info_need_ids, needs_by_id, path_order)
            ),
            closest_canonical_type_codes=tuple(closest_codes),
            why_canonical_types_are_insufficient=insufficiency,
            rationale=rationale,
            confidence=round(confidence if confidence is not None else 0.0, 4),
            provenance=_entity_type_provenance(
                llm_metadata=llm_metadata,
                input_fingerprint=input_fingerprint,
                source_suggestion_index=fallback_index,
            ),
        ),
        [],
        diagnostics,
    )


def _deduplicate_entity_type_candidates(
    candidates: list[EntityTypeCandidate],
    *,
    ontology_by_code: dict[str, EntityTypeDefinition],
    path_order: list[str],
) -> list[EntityTypeCandidate]:
    groups: dict[str, list[EntityTypeCandidate]] = defaultdict(list)

    for candidate in candidates:
        groups[candidate.entity_type_code].append(candidate)

    merged: list[EntityTypeCandidate] = []

    for entity_type_code in sorted(groups):
        group = groups[entity_type_code]

        if len(group) == 1:
            merged.append(group[0])
            continue

        definition = ontology_by_code[entity_type_code]
        information_need_ids = sorted(
            {
                need_id
                for candidate in group
                for need_id in candidate.related_information_need_ids
            }
        )
        path_ids = _order_by_reference(
            {
                path_id
                for candidate in group
                for path_id in candidate.related_target_career_path_ids
            },
            path_order,
        )
        objective_codes = tuple(
            MonitoringObjectiveCode(code)
            for code in _order_objective_codes(
                {
                    objective.value
                    for candidate in group
                    for objective in candidate.supported_monitoring_objectives
                }
            )
        )
        rationales = sorted(
            {
                candidate.rationale
                for candidate in group
                if candidate.rationale
            },
            key=str.casefold,
        )
        discovery_terms = _union_sorted_strings(
            [
                term
                for candidate in group
                for term in candidate.discovery_terms
            ]
        )
        confidence = round(
            sum(candidate.confidence for candidate in group) / len(group),
            4,
        )
        first = sorted(group, key=lambda item: item.candidate_id)[0]

        merged.append(
            EntityTypeCandidate(
                candidate_id=build_entity_type_candidate_id(
                    entity_type_code=entity_type_code,
                    ontology_version=definition.ontology_version,
                ),
                entity_type_code=entity_type_code,
                display_name=definition.display_name,
                related_information_need_ids=tuple(information_need_ids),
                related_target_career_path_ids=tuple(path_ids),
                supported_monitoring_objectives=objective_codes,
                rationale=" | ".join(rationales),
                discovery_terms=tuple(discovery_terms),
                confidence=confidence,
                provenance=first.provenance,
                schema_version=first.schema_version,
            )
        )

    return merged


def _candidate_gap_to_proposed_type(
    *,
    normalized_code: str,
    suggestion: dict[str, Any],
    information_need_ids: list[str],
    needs_by_id: dict[str, InformationNeed],
    path_order: list[str],
    llm_metadata: LLMExecutionMetadata,
    input_fingerprint: str,
    fallback_index: int,
    confidence: float,
    rationale: str,
) -> ProposedEntityType:
    display_name = str(suggestion.get("display_name") or normalized_code.replace("_", " ").title())
    definition = (
        str(suggestion.get("definition") or rationale)
    )

    return ProposedEntityType(
        proposed_code=normalized_code,
        display_name=_normalize_text(display_name, max_length=120),
        definition=_normalize_text(definition, max_length=MAX_ENTITY_TYPE_TEXT_LENGTH),
        broader_group=normalize_entity_type_code(
            suggestion.get("broader_group") or "information_platform"
        ),
        supporting_information_need_ids=tuple(information_need_ids),
        related_target_career_path_ids=tuple(
            _derive_path_ids(information_need_ids, needs_by_id, path_order)
        ),
        closest_canonical_type_codes=(),
        why_canonical_types_are_insufficient=(
            "LLM returned a structurally valid entity_type_code that is not in "
            "the controlled ontology."
        ),
        rationale=rationale,
        confidence=round(confidence, 4),
        provenance=_entity_type_provenance(
            llm_metadata=llm_metadata,
            input_fingerprint=input_fingerprint,
            source_suggestion_index=fallback_index,
        ),
    )


def _normalize_information_need_ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []

    return sorted(
        {
            str(item).strip()
            for item in value
            if item is not None and str(item).strip()
        }
    )


def _normalize_discovery_terms(
    value: Any,
    *,
    max_discovery_terms: int,
) -> tuple[list[str], list[str]]:
    if not isinstance(value, list):
        return [], []

    diagnostics: list[str] = []
    terms = _union_sorted_strings(
        [
            _normalize_text(item, max_length=MAX_DISCOVERY_TERM_LENGTH)
            for item in value
            if item is not None
        ]
    )

    if len(terms) > max_discovery_terms:
        diagnostics.append(
            "Truncated discovery_terms from "
            f"{len(terms)} to {max_discovery_terms}."
        )
        terms = terms[:max_discovery_terms]

    return terms, diagnostics


def _normalize_closest_canonical_codes(value: Any) -> list[str] | None:
    if not isinstance(value, list):
        return []

    resolved: list[str] = []

    for item in value:
        code = resolve_entity_type_code(str(item or ""))

        if code is None:
            return None

        resolved.append(code)

    return _union_sorted_strings(resolved)


def _derive_path_ids(
    information_need_ids: list[str],
    needs_by_id: dict[str, InformationNeed],
    path_order: list[str],
) -> list[str]:
    path_ids = {
        path_id
        for need_id in information_need_ids
        for path_id in needs_by_id[need_id].related_target_career_path_ids
    }
    return _order_by_reference(path_ids, path_order)


def _derive_objective_codes(
    information_need_ids: list[str],
    needs_by_id: dict[str, InformationNeed],
) -> list[MonitoringObjectiveCode]:
    return [
        MonitoringObjectiveCode(code)
        for code in _order_objective_codes(
            {
                needs_by_id[need_id].objective_code.value
                for need_id in information_need_ids
            }
        )
    ]


def _order_by_reference(values: set[str], reference_order: list[str]) -> list[str]:
    order = {value: index for index, value in enumerate(reference_order)}
    return sorted(values, key=lambda value: (order.get(value, 999999), value))


def _order_objective_codes(values: set[str]) -> list[str]:
    order = {
        "opportunity": 0,
        "organization": 1,
        "industry": 2,
        "career_path": 3,
    }
    return sorted(values, key=lambda value: (order.get(value, 999999), value))


def _union_sorted_strings(values: list[str]) -> list[str]:
    by_key: dict[str, str] = {}

    for value in values:
        normalized = re.sub(r"\s+", " ", str(value)).strip()
        key = normalized.casefold()

        if normalized and key not in by_key:
            by_key[key] = normalized

    return [by_key[key] for key in sorted(by_key)]


def _entity_type_boundary_errors(text: str) -> list[str]:
    errors = _boundary_errors(text)

    if _EXECUTABLE_QUERY_PATTERN.search(text):
        errors.append("must not contain executable search query syntax")

    if _PHASE1_CONCRETE_ENTITY_PATTERN.search(text):
        errors.append("must not contain concrete organizations or institutions")

    if _PHASE1_CONCRETE_CJK_ENTITY_PATTERN.search(text):
        errors.append("must not contain concrete organizations or institutions")

    for code in PROHIBITED_ENTITY_TYPE_CODES:
        if re.search(rf"\b{re.escape(code)}\b", text, re.IGNORECASE):
            errors.append("must not use SourceType acquisition methods")
            break

    return sorted(set(errors))


def _entity_type_provenance(
    *,
    llm_metadata: LLMExecutionMetadata,
    input_fingerprint: str,
    source_suggestion_index: int,
) -> dict[str, Any]:
    return {
        "provider": llm_metadata.provider,
        "model": llm_metadata.model,
        "prompt_version": llm_metadata.prompt_version,
        "schema_version": ENTITY_TYPE_CANDIDATE_SCHEMA_VERSION,
        "ontology_version": ENTITY_TYPE_ONTOLOGY_VERSION,
        "input_fingerprint": input_fingerprint,
        "source_suggestion_index": source_suggestion_index,
    }
