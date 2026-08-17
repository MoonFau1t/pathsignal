import json
import re
from typing import Any

from openai import OpenAI

from src.config import (
    ENTITY_DISCOVERY_EXTRACTION_MAX_EVIDENCE_PER_BATCH,
    ENTITY_DISCOVERY_EXTRACTION_MODEL,
    ENTITY_DISCOVERY_EXTRACTION_TEMPERATURE,
    ENTITY_DISCOVERY_MAX_ENTITIES_PER_TYPE,
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_PROVIDER,
)
from src.source_monitoring.entity_classifier import classify_entity_candidate
from src.source_monitoring.entity_discovery_models import (
    EntityCandidate,
    EntityCandidateVerificationStatus,
    EntityDiscoveryEvidence,
    EntityDiscoveryPlan,
    OfficialDomainCandidate,
    OfficialDomainVerificationStatus,
    PrimaryEntityKind,
    RejectedEntityCandidate,
)
from src.source_monitoring.entity_identity import (
    build_entity_candidate_id,
    normalize_domain,
    normalize_evidence_url,
)
from src.source_monitoring.entity_type_ontology import resolve_entity_type_code
from src.source_monitoring.models import EntityTypeCandidate
from src.source_monitoring.prompts import build_entity_candidate_extraction_prompt


class EntityCandidateExtractionError(Exception):
    """
    Raised when Phase 2 cannot use structured extraction output.
    """


class EntityCandidateExtractionClient:
    """
    Optional bounded OpenAI-compatible extractor for Phase 2 evidence.
    """

    def __init__(
        self,
        provider: str,
        api_key: str,
        base_url: str,
        model: str,
        temperature: float = ENTITY_DISCOVERY_EXTRACTION_TEMPERATURE,
    ) -> None:
        self.provider = provider
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.temperature = temperature

        if not self.api_key or self.api_key.startswith("your_"):
            raise EntityCandidateExtractionError(
                "LLM_API_KEY is missing. Add a real DeepSeek-compatible key "
                "before running EntityCandidate extraction."
            )

        self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)

    def generate(
        self,
        *,
        entity_discovery_evidence: tuple[EntityDiscoveryEvidence, ...],
        entity_discovery_plans: tuple[EntityDiscoveryPlan, ...],
        max_entities_per_type: int,
    ) -> dict[str, Any]:
        prompt = build_entity_candidate_extraction_prompt(
            entity_discovery_evidence=entity_discovery_evidence,
            entity_discovery_plans=entity_discovery_plans,
            max_entities_per_type=max_entities_per_type,
        )
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Extract concrete Source Monitoring EntityCandidate "
                        "suggestions from bounded evidence. Return only valid "
                        "JSON. Do not approve sources or decide final identity."
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
            raise EntityCandidateExtractionError(
                "LLM returned an empty EntityCandidate extraction response."
            )
        try:
            parsed = json.loads(_normalize_json_response_text(response_text))
        except json.JSONDecodeError as error:
            raise EntityCandidateExtractionError(
                "EntityCandidate extraction returned invalid JSON after cleanup."
            ) from error
        if not isinstance(parsed, dict):
            raise EntityCandidateExtractionError(
                "EntityCandidate extraction response must be a JSON object."
            )
        return parsed


def parse_entity_candidate_extraction_response(
    parsed: Any,
) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
    if not isinstance(parsed, dict):
        return [], ("EntityCandidate extraction response must be a JSON object.",)

    diagnostics = tuple(
        f"Unexpected extraction top-level field rejected: {key}"
        for key in sorted(set(parsed) - {"entity_candidates"})
    )
    raw_candidates = parsed.get("entity_candidates")
    if not isinstance(raw_candidates, list):
        return [], diagnostics + (
            "EntityCandidate extraction response must contain entity_candidates list.",
        )

    candidates: list[dict[str, Any]] = []
    candidate_diagnostics = list(diagnostics)
    for index, item in enumerate(raw_candidates):
        if isinstance(item, dict):
            candidates.append(item)
        else:
            candidate_diagnostics.append(
                f"Entity candidate at index {index} rejected: item must be an object."
            )

    return candidates, tuple(candidate_diagnostics)


def extract_entity_candidates(
    *,
    entity_discovery_evidence: tuple[EntityDiscoveryEvidence, ...],
    entity_discovery_plans: tuple[EntityDiscoveryPlan, ...],
    entity_type_candidates: tuple[EntityTypeCandidate, ...],
    client: EntityCandidateExtractionClient | None = None,
    max_entities_per_type: int = ENTITY_DISCOVERY_MAX_ENTITIES_PER_TYPE,
    max_evidence_per_batch: int = ENTITY_DISCOVERY_EXTRACTION_MAX_EVIDENCE_PER_BATCH,
    model: str | None = None,
    provider: str | None = None,
    temperature: float = ENTITY_DISCOVERY_EXTRACTION_TEMPERATURE,
) -> tuple[
    tuple[EntityCandidate, ...],
    tuple[RejectedEntityCandidate, ...],
    tuple[str, ...],
]:
    if not entity_discovery_evidence:
        return (), (), ("No EntityDiscoveryEvidence available for extraction.",)

    selected_provider = provider or (client.provider if client is not None else LLM_PROVIDER)
    selected_model = model or (
        client.model if client is not None else ENTITY_DISCOVERY_EXTRACTION_MODEL
    )
    extraction_client = client or EntityCandidateExtractionClient(
        provider=selected_provider,
        api_key=LLM_API_KEY,
        base_url=LLM_BASE_URL,
        model=selected_model,
        temperature=temperature,
    )
    suggestions: list[dict[str, Any]] = []
    diagnostics: list[str] = []
    evidence_batches = _evidence_batches(
        entity_discovery_evidence,
        max_evidence_per_batch=max_evidence_per_batch,
    )
    diagnostics.append(
        "EntityCandidate extraction batches: "
        + ", ".join(str(len(batch)) for batch in evidence_batches)
    )
    for batch_index, evidence_batch in enumerate(evidence_batches):
        parsed = extraction_client.generate(
            entity_discovery_evidence=evidence_batch,
            entity_discovery_plans=entity_discovery_plans,
            max_entities_per_type=max_entities_per_type,
        )
        batch_suggestions, parse_diagnostics = (
            parse_entity_candidate_extraction_response(parsed)
        )
        suggestions.extend(batch_suggestions)
        diagnostics.extend(
            f"batch {batch_index}: {diagnostic}"
            for diagnostic in parse_diagnostics
        )
    candidates, rejected, validation_diagnostics = validate_entity_candidate_suggestions(
        suggestions=suggestions,
        entity_discovery_evidence=entity_discovery_evidence,
        entity_discovery_plans=entity_discovery_plans,
        entity_type_candidates=entity_type_candidates,
        max_entities_per_type=max_entities_per_type,
    )
    return candidates, rejected, tuple(diagnostics) + validation_diagnostics


def validate_entity_candidate_suggestions(
    *,
    suggestions: list[dict[str, Any]],
    entity_discovery_evidence: tuple[EntityDiscoveryEvidence, ...],
    entity_discovery_plans: tuple[EntityDiscoveryPlan, ...],
    entity_type_candidates: tuple[EntityTypeCandidate, ...],
    max_entities_per_type: int,
) -> tuple[
    tuple[EntityCandidate, ...],
    tuple[RejectedEntityCandidate, ...],
    tuple[str, ...],
]:
    evidence_by_id = {item.evidence_id: item for item in entity_discovery_evidence}
    candidate_by_type = {
        item.entity_type_code: item
        for item in entity_type_candidates
    }
    allowed_fields = {
        "canonical_name",
        "names_by_language",
        "primary_entity_kind",
        "entity_type_codes",
        "classification_facets",
        "official_domain_candidates",
        "supporting_evidence_ids",
        "geographic_scope",
        "rationale",
        "confidence",
        "ambiguity_notes",
        "likely_entity_type_labels",
        "identity_group_key",
    }
    accepted: list[EntityCandidate] = []
    rejected: list[RejectedEntityCandidate] = []
    diagnostics: list[str] = []
    per_type_counts: dict[str, int] = {}

    for index, suggestion in enumerate(suggestions):
        errors: list[str] = []
        extra_fields = sorted(set(suggestion) - allowed_fields)
        if extra_fields:
            errors.append(f"unsupported fields: {extra_fields}")

        canonical_name = str(suggestion.get("canonical_name", "")).strip()
        if not canonical_name:
            errors.append("canonical_name is required")
        if _looks_like_false_positive_name(canonical_name):
            errors.append("candidate name looks like an article, job, or category")

        evidence_ids = tuple(
            sorted(
                {
                    str(item)
                    for item in suggestion.get("supporting_evidence_ids", [])
                    if item is not None
                }
            )
        )
        if not evidence_ids:
            errors.append("supporting_evidence_ids are required")
        elif any(evidence_id not in evidence_by_id for evidence_id in evidence_ids):
            errors.append("supporting_evidence_ids contain unknown IDs")

        if evidence_ids and _evidence_is_job_or_article(canonical_name, evidence_ids, evidence_by_id):
            errors.append("supporting evidence appears to be a job page or article")

        entity_type_codes = tuple(
            sorted(
                {
                    resolved
                    for raw in suggestion.get("entity_type_codes", [])
                    for resolved in [resolve_entity_type_code(str(raw))]
                    if resolved is not None
                }
            )
        )
        if not entity_type_codes:
            errors.append("entity_type_codes must resolve to controlled ontology")
        elif any(code not in candidate_by_type for code in entity_type_codes):
            errors.append("entity_type_codes are not selected Phase 1 candidates")

        try:
            primary_kind = PrimaryEntityKind(
                str(suggestion.get("primary_entity_kind", ""))
            )
        except ValueError:
            primary_kind = PrimaryEntityKind.INFORMATION_PLATFORM
            diagnostics.append(
                f"Entity candidate at index {index} supplied uncontrolled "
                "primary_entity_kind; deterministic classification will derive "
                "the controlled primary kind from entity_type_codes."
            )

        confidence = _bounded_confidence(suggestion.get("confidence"))
        if confidence is None:
            errors.append("confidence must be between 0 and 1")
            confidence = 0.0

        if errors:
            rejected.append(
                RejectedEntityCandidate(
                    original_candidate=dict(suggestion),
                    rejection_reason="; ".join(errors),
                    supporting_evidence_ids=evidence_ids,
                    diagnostics=tuple(errors),
                    source_extraction_index=index,
                )
            )
            continue

        if any(
            per_type_counts.get(code, 0) >= max_entities_per_type
            for code in entity_type_codes
        ):
            rejected.append(
                RejectedEntityCandidate(
                    original_candidate=dict(suggestion),
                    rejection_reason="entity limit reached for entity type",
                    supporting_evidence_ids=evidence_ids,
                    diagnostics=("entity limit reached for entity type",),
                    source_extraction_index=index,
                )
            )
            continue

        related_type_candidate_ids = tuple(
            sorted(candidate_by_type[code].candidate_id for code in entity_type_codes)
        )
        related_need_ids = tuple(
            sorted(
                {
                    need_id
                    for code in entity_type_codes
                    for need_id in candidate_by_type[code].related_information_need_ids
                }
            )
        )
        related_path_ids = tuple(
            sorted(
                {
                    path_id
                    for code in entity_type_codes
                    for path_id in candidate_by_type[code].related_target_career_path_ids
                }
            )
        )
        official_domains = _official_domain_candidates(
            suggestion.get("official_domain_candidates"),
            evidence_by_id=evidence_by_id,
        )
        verified_domains = tuple(
            sorted(
                {
                    item.domain
                    for item in official_domains
                    if item.verification_status
                    == OfficialDomainVerificationStatus.VERIFIED_OFFICIAL
                }
            )
        )
        evidence_urls = tuple(
            sorted({evidence_by_id[item].url for item in evidence_ids})
        )
        candidate = EntityCandidate(
            entity_id=build_entity_candidate_id(
                canonical_name=canonical_name,
                official_domains=verified_domains,
                entity_type_codes=entity_type_codes,
            ),
            canonical_name=canonical_name,
            names_by_language=_names_by_language(suggestion, canonical_name),
            primary_entity_kind=primary_kind,
            entity_type_codes=entity_type_codes,
            classification_facets=_classification_facets(
                suggestion.get("classification_facets")
            ),
            related_entity_type_candidate_ids=related_type_candidate_ids,
            related_information_need_ids=related_need_ids,
            related_target_career_path_ids=related_path_ids,
            official_domain_candidates=official_domains,
            evidence_ids=evidence_ids,
            evidence_urls=evidence_urls,
            geographic_scope=str(suggestion.get("geographic_scope", "")).strip(),
            rationale=str(suggestion.get("rationale", "")).strip(),
            confidence=confidence,
            verification_status=EntityCandidateVerificationStatus.EVIDENCE_SUPPORTED,
            provenance={
                "source": "structured_extraction",
                "source_extraction_index": index,
                "identity_group_key": str(
                    suggestion.get("identity_group_key", "")
                ).strip(),
            },
        )
        accepted.append(classify_entity_candidate(candidate))
        for code in entity_type_codes:
            per_type_counts[code] = per_type_counts.get(code, 0) + 1

    ordered = tuple(sorted(accepted, key=lambda item: item.entity_id))
    return ordered, tuple(rejected), tuple(diagnostics)


def _official_domain_candidates(
    value: Any,
    *,
    evidence_by_id: dict[str, EntityDiscoveryEvidence],
) -> tuple[OfficialDomainCandidate, ...]:
    if not isinstance(value, list):
        return ()

    candidates: list[OfficialDomainCandidate] = []
    evidence_urls = {item.url for item in evidence_by_id.values()}
    for item in value:
        if not isinstance(item, dict):
            continue

        domain = normalize_domain(item.get("domain"))
        if not domain:
            continue

        evidence_url = normalize_evidence_url(item.get("evidence_url"))
        if evidence_url and evidence_url not in evidence_urls:
            evidence_url = evidence_url

        try:
            status = OfficialDomainVerificationStatus(
                str(item.get("verification_status", "unresolved"))
            )
        except ValueError:
            status = OfficialDomainVerificationStatus.UNRESOLVED

        candidates.append(
            OfficialDomainCandidate(
                domain=domain,
                evidence_url=evidence_url,
                confidence=float(item.get("confidence", 0.0)),
                verification_status=status,
                reason=str(item.get("reason", "")),
            )
        )

    return tuple(
        sorted(
            candidates,
            key=lambda item: (item.domain, item.verification_status.value),
        )
    )


def _evidence_batches(
    evidence_items: tuple[EntityDiscoveryEvidence, ...],
    *,
    max_evidence_per_batch: int,
) -> tuple[tuple[EntityDiscoveryEvidence, ...], ...]:
    batch_size = max(1, max_evidence_per_batch)
    ordered = tuple(sorted(evidence_items, key=lambda item: item.evidence_id))
    return tuple(
        ordered[index:index + batch_size]
        for index in range(0, len(ordered), batch_size)
    )


def _names_by_language(
    suggestion: dict[str, Any],
    canonical_name: str,
) -> dict[str, tuple[str, ...]]:
    names: dict[str, set[str]] = {}
    raw_names = suggestion.get("names_by_language")
    if isinstance(raw_names, dict):
        for language, values in raw_names.items():
            if not isinstance(values, list):
                continue
            cleaned = {
                str(value).strip()
                for value in values
                if str(value).strip()
            }
            if cleaned:
                names[str(language)] = cleaned

    if not names:
        language = "zh" if any("\u4e00" <= char <= "\u9fff" for char in canonical_name) else "en"
        names[language] = {canonical_name}

    return {
        language: tuple(sorted(values))
        for language, values in sorted(names.items())
    }


def _classification_facets(value: Any) -> dict[str, tuple[str, ...]]:
    if not isinstance(value, dict):
        return {}

    facets: dict[str, tuple[str, ...]] = {}
    for dimension, values in value.items():
        if not isinstance(values, list):
            continue
        cleaned = tuple(sorted({str(item) for item in values if str(item).strip()}))
        if cleaned:
            facets[str(dimension)] = cleaned

    return facets


def _bounded_confidence(value: Any) -> float | None:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return None

    if 0 <= confidence <= 1:
        return confidence

    return None


def _looks_like_false_positive_name(value: str) -> bool:
    if re.search(
        r"\b(article|headline|report:|top .+ to watch|jobs?|openings?|"
        r"apply now|category)\b|"
        r"招聘|职位|立即申请",
        value,
        flags=re.IGNORECASE,
    ):
        return True

    return len(value.split()) > 14


def _evidence_is_job_or_article(
    canonical_name: str,
    evidence_ids: tuple[str, ...],
    evidence_by_id: dict[str, EntityDiscoveryEvidence],
) -> bool:
    name_key = canonical_name.strip().casefold()
    for evidence_id in evidence_ids:
        evidence = evidence_by_id[evidence_id]
        evidence_text = f"{evidence.title} {evidence.snippet} {evidence.url}"
        if re.search(
            r"/jobs?/|/careers?/|\b(apply now|job opening|job posting)\b|"
            r"招聘|职位申请",
            evidence_text,
            flags=re.IGNORECASE,
        ):
            return True
        if name_key and name_key == evidence.title.strip().casefold():
            return True

    return False


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
        return fenced_match.group(1).strip()
    return stripped_text
