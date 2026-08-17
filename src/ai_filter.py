import hashlib
import json
from typing import Any

from openai import OpenAI

from src.models import (
    AIFilterExecutionReport,
    AIFilterResult,
    RawItem,
    RawItemFilterStatus,
    SignalCategory,
    TargetCareerPath,
    UserProfile,
)


class AIFilterError(Exception):
    """
    Raised when AI Filter execution fails.
    """


class AIFilterClient:
    """
    AI Filter client.

    It can run in two modes:
    - dry_run=True: use local rule-based filtering
    - dry_run=False: call an OpenAI-compatible LLM provider

    For Phase 9, DeepSeek is the preferred provider.
    """

    def __init__(
        self,
        provider: str,
        api_key: str,
        base_url: str,
        model: str,
        dry_run: bool = True,
    ) -> None:
        self.provider = provider
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.dry_run = dry_run

        self.client = None

        if not self.dry_run:
            if not self.api_key or self.api_key.startswith("your_"):
                raise AIFilterError(
                    "LLM_API_KEY is missing. Add your real DeepSeek API key "
                    "to .env, or set AI_FILTER_DRY_RUN=true for local testing."
                )

            self.client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
            )

    def filter_item(
        self,
        raw_item: RawItem,
        user_profile: UserProfile,
        target_career_paths: list[TargetCareerPath],
    ) -> AIFilterResult:
        """
        Filter one RawItem.
        """

        if self.dry_run:
            return self._dry_run_filter(
                raw_item=raw_item,
                target_career_paths=target_career_paths,
            )

        return self._llm_filter(
            raw_item=raw_item,
            user_profile=user_profile,
            target_career_paths=target_career_paths,
        )

    def _dry_run_filter(
        self,
        raw_item: RawItem,
        target_career_paths: list[TargetCareerPath],
    ) -> AIFilterResult:
        """
        Local rule-based filter used for development.

        This is not real AI.
        It only simulates the shape of AI Filter output.
        """

        text = (
            f"{raw_item.title} "
            f"{raw_item.organization} "
            f"{raw_item.raw_text} "
            f"{raw_item.url}"
        ).lower()

        hard_negative_terms = [
            "bootcamp",
            "certificate",
            "course",
            "udemy",
            "coursera",
            "senior director",
            "vice president",
            "vp ",
            "partner",
            "principal",
            "head of",
            "unpaid",
        ]

        positive_terms = [
            "analyst",
            "associate",
            "strategy",
            "consulting",
            "venture",
            "capital",
            "startup",
            "investment",
            "ai",
            "artificial intelligence",
            "market",
            "research",
            "funding",
            "raises",
            "hiring",
            "career",
            "job",
            "role",
        ]

        matched_negative_terms = [
            term for term in hard_negative_terms if term in text
        ]

        matched_positive_terms = [
            term for term in positive_terms if term in text
        ]

        matched_career_path_ids = _match_career_paths(
            text=text,
            target_career_paths=target_career_paths,
        )

        if matched_negative_terms:
            return AIFilterResult(
                raw_item_fingerprint=_fingerprint_raw_item(raw_item),
                title=raw_item.title,
                url=raw_item.url,
                is_relevant=False,
                confidence=0.85,
                reason=(
                    "Dry-run filter rejected this item because it matched "
                    f"negative terms: {matched_negative_terms}"
                ),
                suggested_category=SignalCategory.UNKNOWN,
                matched_career_path_ids=matched_career_path_ids,
                action="drop",
                metadata={
                    "mode": "dry_run",
                    "matched_negative_terms": matched_negative_terms,
                    "matched_positive_terms": matched_positive_terms,
                },
            )

        if matched_positive_terms or matched_career_path_ids:
            return AIFilterResult(
                raw_item_fingerprint=_fingerprint_raw_item(raw_item),
                title=raw_item.title,
                url=raw_item.url,
                is_relevant=True,
                confidence=0.72,
                reason=(
                    "Dry-run filter kept this item because it matched "
                    "career-related terms or target career paths."
                ),
                suggested_category=_guess_signal_category(text),
                matched_career_path_ids=matched_career_path_ids,
                action="keep",
                metadata={
                    "mode": "dry_run",
                    "matched_negative_terms": matched_negative_terms,
                    "matched_positive_terms": matched_positive_terms,
                },
            )

        return AIFilterResult(
            raw_item_fingerprint=_fingerprint_raw_item(raw_item),
            title=raw_item.title,
            url=raw_item.url,
            is_relevant=False,
            confidence=0.6,
            reason=(
                "Dry-run filter rejected this item because it did not match "
                "career-related terms or target career paths."
            ),
            suggested_category=SignalCategory.UNKNOWN,
            matched_career_path_ids=[],
            action="drop",
            metadata={
                "mode": "dry_run",
                "matched_negative_terms": matched_negative_terms,
                "matched_positive_terms": matched_positive_terms,
            },
        )

    def _llm_filter(
        self,
        raw_item: RawItem,
        user_profile: UserProfile,
        target_career_paths: list[TargetCareerPath],
    ) -> AIFilterResult:
        """
        Use an OpenAI-compatible LLM provider to judge whether one RawItem is relevant.
        """

        if self.client is None:
            raise AIFilterError("LLM client is not initialized.")

        prompt = _build_filter_prompt(
            raw_item=raw_item,
            user_profile=user_profile,
            target_career_paths=target_career_paths,
        )

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an AI career intelligence filter. "
                        "You judge whether a raw search result is useful "
                        "for the user's career intelligence workflow. "
                        "Return only valid JSON."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            response_format={
                "type": "json_object",
            },
            stream=False,
        )

        response_text = response.choices[0].message.content

        if response_text is None:
            raise AIFilterError("LLM returned empty response.")

        try:
            parsed = json.loads(response_text)
        except json.JSONDecodeError as error:
            raise AIFilterError(
                f"AI Filter returned invalid JSON: {response_text}"
            ) from error

        return _build_filter_result_from_ai_json(
            raw_item=raw_item,
            parsed=parsed,
        )


def execute_ai_filter(
    raw_items: list[RawItem],
    user_profile: UserProfile,
    target_career_paths: list[TargetCareerPath],
    client: AIFilterClient,
) -> AIFilterExecutionReport:
    """
    Execute AI Filter over RawItems.

    Returns:
    - filtered_raw_items
    - ai_filter_results
    - executed item count
    """

    filtered_raw_items: list[RawItem] = []
    ai_filter_results: list[AIFilterResult] = []
    raw_item_statuses: list[RawItemFilterStatus] = []

    for raw_item_index, raw_item in enumerate(raw_items):
        try:
            filter_result = client.filter_item(
                raw_item=raw_item,
                user_profile=user_profile,
                target_career_paths=target_career_paths,
            )
        except Exception as error:
            filter_result = AIFilterResult(
                raw_item_fingerprint=_fingerprint_raw_item(raw_item),
                title=raw_item.title,
                url=raw_item.url,
                is_relevant=False,
                confidence=0.0,
                reason=f"AI Filter failed: {error}",
                suggested_category=SignalCategory.UNKNOWN,
                matched_career_path_ids=[],
                action="review",
                metadata={
                    "filter_error": str(error),
                },
            )

        ai_filter_results.append(filter_result)

        if filter_result.is_relevant:
            filtered_raw_items.append(raw_item)

        if filter_result.metadata.get("filter_error"):
            status = "failed"
        elif filter_result.is_relevant:
            status = "processed_accepted"
        else:
            status = "processed_rejected"

        raw_item_statuses.append(
            RawItemFilterStatus(
                raw_item_fingerprint=_fingerprint_raw_item(raw_item),
                raw_item_index=raw_item_index,
                source_type=raw_item.source_type,
                title=raw_item.title,
                url=raw_item.url,
                status=status,
                reason=filter_result.reason,
                is_relevant=filter_result.is_relevant,
                metadata={
                    "ai_filter_action": filter_result.action,
                    "ai_filter_confidence": filter_result.confidence,
                },
            )
        )

    return AIFilterExecutionReport(
        filtered_raw_items=filtered_raw_items,
        ai_filter_results=ai_filter_results,
        raw_item_statuses=raw_item_statuses,
        executed_count=len(raw_items),
    )


def _build_filter_prompt(
    raw_item: RawItem,
    user_profile: UserProfile,
    target_career_paths: list[TargetCareerPath],
) -> str:
    """
    Build prompt for one AI filtering decision.
    """

    career_paths_payload = [
        {
            "path_id": path.path_id,
            "title": path.title,
            "description": path.description,
            "keywords": path.keywords,
            "suggested_roles": path.suggested_roles,
        }
        for path in target_career_paths
    ]

    raw_item_payload = {
        "source_type": raw_item.source_type.value,
        "title": raw_item.title,
        "organization": raw_item.organization,
        "url": raw_item.url,
        "published_at": raw_item.published_at,
        "raw_text": raw_item.raw_text,
        "metadata": raw_item.metadata,
    }

    user_payload = {
        "background_summary": user_profile.background_summary,
        "skills": user_profile.skills,
        "interests": user_profile.interests,
        "preferred_roles": user_profile.preferred_roles,
        "preferred_locations": user_profile.preferred_locations,
        "constraints": user_profile.constraints,
    }

    return f"""
Decide whether this RawItem is useful for the user's career intelligence workflow.

UserProfile:
{json.dumps(user_payload, ensure_ascii=False, indent=2)}

TargetCareerPaths:
{json.dumps(career_paths_payload, ensure_ascii=False, indent=2)}

RawItem:
{json.dumps(raw_item_payload, ensure_ascii=False, indent=2)}

Filtering rules:
- Keep job opportunities that match the target career paths.
- Keep industry signals that may imply future opportunities, such as funding, expansion, new AI practice, hiring growth, market demand, or startup momentum.
- Drop course ads, bootcamps, generic SEO pages, irrelevant senior executive roles, unrelated news, or vague pages with no career value.
- If uncertain but potentially useful, mark action as "review" and set is_relevant to true only when it is worth human review.

Return only JSON with this schema:
{{
  "is_relevant": true,
  "confidence": 0.0,
  "reason": "short explanation",
  "suggested_category": "job | news | company | funding | market_trend | unknown",
  "matched_career_path_ids": ["path_id"],
  "action": "keep | drop | review"
}}
""".strip()


def _build_filter_result_from_ai_json(
    raw_item: RawItem,
    parsed: dict[str, Any],
) -> AIFilterResult:
    """
    Convert AI JSON output into AIFilterResult.
    """

    suggested_category = _parse_signal_category(
        parsed.get("suggested_category", "unknown")
    )

    confidence = parsed.get("confidence", 0.0)

    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.0

    confidence = min(max(confidence, 0.0), 1.0)

    matched_career_path_ids = parsed.get("matched_career_path_ids", [])

    if not isinstance(matched_career_path_ids, list):
        matched_career_path_ids = []

    return AIFilterResult(
        raw_item_fingerprint=_fingerprint_raw_item(raw_item),
        title=raw_item.title,
        url=raw_item.url,
        is_relevant=bool(parsed.get("is_relevant", False)),
        confidence=confidence,
        reason=str(parsed.get("reason", "")),
        suggested_category=suggested_category,
        matched_career_path_ids=[
            str(path_id)
            for path_id in matched_career_path_ids
        ],
        action=str(parsed.get("action", "review")),
        metadata={
            "mode": "openai",
            "raw_ai_response": parsed,
        },
    )


def _parse_signal_category(value: str) -> SignalCategory:
    """
    Convert string into SignalCategory safely.
    """

    try:
        return SignalCategory(value)
    except ValueError:
        return SignalCategory.UNKNOWN


def _fingerprint_raw_item(raw_item: RawItem) -> str:
    """
    Create a stable fingerprint for one RawItem.
    """

    fingerprint_source = (
        f"{raw_item.source_type.value}|"
        f"{raw_item.title}|"
        f"{raw_item.url}|"
        f"{raw_item.raw_text[:200]}"
    )

    return hashlib.sha256(
        fingerprint_source.encode("utf-8")
    ).hexdigest()[:16]


def _match_career_paths(
    text: str,
    target_career_paths: list[TargetCareerPath],
) -> list[str]:
    """
    Roughly match RawItem text against target career paths.
    """

    matched_ids: list[str] = []

    for career_path in target_career_paths:
        search_terms = (
            career_path.keywords
            + career_path.suggested_roles
            + career_path.search_seed_terms
            + [career_path.title]
        )

        for term in search_terms:
            if term and term.lower() in text:
                matched_ids.append(career_path.path_id)
                break

    return matched_ids


def _guess_signal_category(text: str) -> SignalCategory:
    """
    Guess signal category in dry-run mode.
    """

    if any(term in text for term in ["hiring", "job", "role", "career", "analyst"]):
        return SignalCategory.JOB

    if any(term in text for term in ["raised", "funding", "series a", "series b"]):
        return SignalCategory.FUNDING

    if any(term in text for term in ["market", "trend", "demand"]):
        return SignalCategory.MARKET_TREND

    if any(term in text for term in ["company", "startup", "launches", "expands"]):
        return SignalCategory.COMPANY

    if any(term in text for term in ["news", "announced", "report"]):
        return SignalCategory.NEWS

    return SignalCategory.UNKNOWN
