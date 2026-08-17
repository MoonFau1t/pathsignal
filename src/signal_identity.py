import hashlib

from src.models import RawItem


def build_signal_id(raw_item: RawItem) -> str:
    """
    Build stable CareerSignal ID from source type, title, and URL.
    """

    source = (
        f"{raw_item.source_type.value}|"
        f"{raw_item.title}|"
        f"{raw_item.url}"
    )

    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]

    return f"signal_{digest}"
