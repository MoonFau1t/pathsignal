from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse

from src.database.planning_identity import hash_canonical_value
from src.source_monitoring.entity_identity import normalize_domain
from src.source_monitoring.source_discovery_models import (
    CANDIDATE_SOURCE_SCHEMA_VERSION,
    REJECTED_CANDIDATE_SOURCE_SCHEMA_VERSION,
    SOURCE_DISCOVERY_EVIDENCE_SCHEMA_VERSION,
    SOURCE_DISCOVERY_EXECUTION_SCHEMA_VERSION,
    SOURCE_DISCOVERY_PLAN_SCHEMA_VERSION,
    SOURCE_DISCOVERY_PLANNING_RESULT_SCHEMA_VERSION,
    SOURCE_DISCOVERY_RESULT_SCHEMA_VERSION,
    SOURCE_DISCOVERY_URL_NORMALIZATION_POLICY_VERSION,
    SourceFormatHint,
    SourceRole,
)


_TRACKING_QUERY_PREFIXES = ("utm_",)
_TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "igshid",
    "mc_cid",
    "mc_eid",
    "msclkid",
}


def normalize_source_url(value: str | None) -> str:
    if value is None:
        return ""

    text = str(value).strip()
    if not text:
        return ""

    parsed = urlparse(text if "://" in text else f"https://{text}")
    scheme = (parsed.scheme or "https").lower()
    host = (parsed.hostname or "").casefold().rstrip(".")
    if host.startswith("www."):
        host = host[4:]

    port = parsed.port
    netloc = host
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{host}:{port}"

    path = quote(parsed.path or "/", safe="/:%@+~#=&;,-._!$'()*[]")
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    query_items = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key not in _TRACKING_QUERY_KEYS
        and not any(key.startswith(prefix) for prefix in _TRACKING_QUERY_PREFIXES)
    ]
    query = urlencode(sorted(query_items), doseq=True)

    normalized = urlunparse((scheme, netloc, path, "", query, ""))
    if path == "/" and not query:
        normalized = urlunparse((scheme, netloc, "", "", "", ""))
    return normalized


def root_domain_from_url(value: str | None) -> str:
    return normalize_domain(value)


def infer_source_format_hint(url: str) -> SourceFormatHint:
    normalized = normalize_source_url(url).casefold()
    path = urlparse(normalized).path.casefold()
    if path.endswith((".atom", "/atom")) or "format=atom" in normalized:
        return SourceFormatHint.ATOM_CANDIDATE
    if path.endswith((".rss", ".xml", "/rss", "/feed")) or "format=rss" in normalized:
        return SourceFormatHint.RSS_CANDIDATE
    if normalized:
        return SourceFormatHint.HTML_PAGE
    return SourceFormatHint.UNKNOWN


def equivalent_query_key(
    *,
    entity_id: str,
    source_role: SourceRole,
    language: str,
    strategy: str,
    query: str,
    domain_constraint: str | None,
) -> tuple[str, str, str, str, str, str]:
    return (
        entity_id,
        source_role.value,
        language,
        strategy,
        " ".join(query.casefold().split()),
        normalize_domain(domain_constraint),
    )


def build_source_discovery_plan_id(
    *,
    entity_id: str,
    source_role: SourceRole,
    strategy: str,
    query_language: str,
    query: str,
    domain_constraint: str | None,
    schema_version: str = SOURCE_DISCOVERY_PLAN_SCHEMA_VERSION,
) -> str:
    digest = hash_canonical_value(
        {
            "schema_version": schema_version,
            "entity_id": entity_id,
            "source_role": source_role.value,
            "strategy": strategy,
            "query_language": query_language,
            "query": " ".join(query.split()),
            "domain_constraint": normalize_domain(domain_constraint),
        }
    )
    return f"source_plan_{digest[:16]}"


def build_source_discovery_execution_id(
    *,
    plan_id: str,
    provider: str,
    schema_version: str = SOURCE_DISCOVERY_EXECUTION_SCHEMA_VERSION,
) -> str:
    digest = hash_canonical_value(
        {
            "schema_version": schema_version,
            "plan_id": plan_id,
            "provider": provider,
        }
    )
    return f"source_exec_{digest[:16]}"


def build_source_discovery_evidence_id(
    *,
    execution_id: str,
    plan_id: str,
    result_rank: int,
    url: str,
    title: str,
    schema_version: str = SOURCE_DISCOVERY_EVIDENCE_SCHEMA_VERSION,
) -> str:
    digest = hash_canonical_value(
        {
            "schema_version": schema_version,
            "execution_id": execution_id,
            "plan_id": plan_id,
            "result_rank": result_rank,
            "normalized_url": normalize_source_url(url),
            "title": title.strip(),
        }
    )
    return f"source_evidence_{digest[:16]}"


def build_candidate_source_id(
    *,
    entity_id: str,
    normalized_url: str,
    source_role: SourceRole,
    schema_version: str = CANDIDATE_SOURCE_SCHEMA_VERSION,
) -> str:
    digest = hash_canonical_value(
        {
            "schema_version": schema_version,
            "entity_id": entity_id,
            "normalized_url": normalize_source_url(normalized_url),
            "source_role": source_role.value,
        }
    )
    return f"candidate_source_{digest[:16]}"


def build_rejected_candidate_source_id(
    *,
    entity_id: str,
    url: str,
    reason: str,
    schema_version: str = REJECTED_CANDIDATE_SOURCE_SCHEMA_VERSION,
) -> str:
    digest = hash_canonical_value(
        {
            "schema_version": schema_version,
            "entity_id": entity_id,
            "normalized_url": normalize_source_url(url),
            "reason": reason,
        }
    )
    return f"rejected_source_{digest[:16]}"


def build_source_discovery_planning_input_fingerprint(**payload: Any) -> str:
    return hash_canonical_value(
        {
            "schema_version": SOURCE_DISCOVERY_PLANNING_RESULT_SCHEMA_VERSION,
            **payload,
        }
    )


def build_source_discovery_planning_output_hash(**payload: Any) -> str:
    return hash_canonical_value(
        {
            "schema_version": SOURCE_DISCOVERY_PLANNING_RESULT_SCHEMA_VERSION,
            **payload,
        }
    )


def build_source_discovery_execution_input_fingerprint(**payload: Any) -> str:
    return hash_canonical_value(
        {
            "schema_version": SOURCE_DISCOVERY_RESULT_SCHEMA_VERSION,
            "url_normalization_policy_version": (
                SOURCE_DISCOVERY_URL_NORMALIZATION_POLICY_VERSION
            ),
            **payload,
        }
    )


def build_source_discovery_output_hash(**payload: Any) -> str:
    return hash_canonical_value(
        {
            "schema_version": SOURCE_DISCOVERY_RESULT_SCHEMA_VERSION,
            **payload,
        }
    )
