from dataclasses import asdict, is_dataclass
from enum import Enum
import hashlib
import json
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from src.models import RawItem


TRACKING_QUERY_PARAMETERS = {
    "fbclid",
    "gclid",
    "igshid",
    "mc_cid",
    "mc_eid",
    "msclkid",
}

EXTERNAL_ID_KEYS = (
    "external_id",
    "source_item_id",
    "result_id",
    "entry_id",
    "guid",
    "id",
)

PROVIDER_KEYS = (
    "provider",
    "source_name",
    "feed_name",
    "website_name",
)

CROSS_METHOD_URL_IDENTITY_SOURCE_TYPES = frozenset(
    {"rss", "atom", "selected_website"}
)


def serialize_raw_item(raw_item: RawItem) -> str:
    """
    Serialize a RawItem snapshot as deterministic JSON.
    """

    payload = _to_json_value(raw_item.to_dict())

    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonicalize_url(url: str | None) -> str | None:
    """
    Conservatively canonicalize URLs without network lookups or redirects.
    """

    if url is None:
        return None

    stripped_url = str(url).strip()

    if not stripped_url:
        return None

    parsed_url = urlsplit(stripped_url)

    if not parsed_url.scheme or not parsed_url.netloc:
        return stripped_url

    scheme = parsed_url.scheme.lower()
    hostname = parsed_url.hostname.lower() if parsed_url.hostname else ""
    netloc = hostname

    if parsed_url.port is not None:
        netloc = f"{netloc}:{parsed_url.port}"

    path = parsed_url.path or ""

    if path == "/":
        path = ""
    elif path.endswith("/"):
        path = path.rstrip("/")

    query_parameters = [
        (key, value)
        for key, value in parse_qsl(parsed_url.query, keep_blank_values=True)
        if not _is_tracking_parameter(key)
    ]
    query = urlencode(sorted(query_parameters), doseq=True)

    return urlunsplit((scheme, netloc, path, query, ""))


def fingerprint_raw_item(raw_item: RawItem) -> str:
    """
    Build a stable raw-source fingerprint for one RawItem.
    """

    source_type = _source_type_value(raw_item)
    provider = extract_provider(raw_item) or ""
    external_id = extract_external_id(raw_item)
    canonical_url = canonicalize_url(raw_item.url)

    if (
        canonical_url
        and source_type in CROSS_METHOD_URL_IDENTITY_SOURCE_TYPES
    ):
        identity_kind = "canonical_url"
        identity_value = canonical_url
        identity_context = {}
    elif external_id:
        identity_kind = "external_id"
        identity_value = external_id
        identity_context = {
            "provider": provider,
            "source_type": source_type,
        }
    elif canonical_url:
        identity_kind = "canonical_url"
        identity_value = canonical_url
        identity_context = {
            "provider": provider,
            "source_type": source_type,
        }
    else:
        identity_kind = "fallback"
        identity_value = "|".join(
            [
                _normalized_text(raw_item.title),
                _normalized_text(raw_item.organization),
                _normalized_text(raw_item.published_at),
            ]
        )
        identity_context = {
            "provider": provider,
            "source_type": source_type,
        }

    fingerprint_input = json.dumps(
        {
            "identity_kind": identity_kind,
            "identity_value": identity_value,
            **identity_context,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    return hashlib.sha256(fingerprint_input.encode("utf-8")).hexdigest()


def extract_provider(raw_item: RawItem) -> str | None:
    metadata = _metadata_dict(raw_item)

    for key in PROVIDER_KEYS:
        value = metadata.get(key)

        if value is not None and str(value).strip():
            return str(value).strip()

    return None


def extract_external_id(raw_item: RawItem) -> str | None:
    metadata = _metadata_dict(raw_item)

    for key in EXTERNAL_ID_KEYS:
        value = metadata.get(key)

        if value is not None and str(value).strip():
            return str(value).strip()

    raw_result = metadata.get("raw_result")

    if isinstance(raw_result, dict):
        for key in EXTERNAL_ID_KEYS:
            value = raw_result.get(key)

            if value is not None and str(value).strip():
                return str(value).strip()

    raw_entry = metadata.get("raw_entry")

    if isinstance(raw_entry, dict):
        for key in EXTERNAL_ID_KEYS:
            value = raw_entry.get(key)

            if value is not None and str(value).strip():
                return str(value).strip()

    return None


def _is_tracking_parameter(key: str) -> bool:
    normalized_key = key.lower()

    return (
        normalized_key.startswith("utm_")
        or normalized_key in TRACKING_QUERY_PARAMETERS
    )


def _source_type_value(raw_item: RawItem) -> str:
    source_type = raw_item.source_type

    if isinstance(source_type, Enum):
        return str(source_type.value)

    return str(source_type)


def _metadata_dict(raw_item: RawItem) -> dict[str, Any]:
    if isinstance(raw_item.metadata, dict):
        return raw_item.metadata

    return {}


def _normalized_text(value: str | None) -> str:
    if value is None:
        return ""

    return " ".join(str(value).strip().casefold().split())


def _to_json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value

    if is_dataclass(value) and not isinstance(value, type):
        return _to_json_value(asdict(value))

    if isinstance(value, dict):
        return {
            str(key): _to_json_value(item)
            for key, item in value.items()
        }

    if isinstance(value, list):
        return [
            _to_json_value(item)
            for item in value
        ]

    if isinstance(value, tuple):
        return [
            _to_json_value(item)
            for item in value
        ]

    return value
