from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable

from openai import OpenAI

from src.career_signal_priority import ScoredCareerSignal
from src.models import SignalCategory, TargetCareerPath
from src.priority_assessment import AssessmentProfile


CAREER_INTELLIGENCE_INTERPRETATION_SCHEMA_VERSION = (
    "career_intelligence_interpretation_v1"
)

MAX_THEMES = 5
MAX_KEY_DEVELOPMENTS = 8
MAX_CAREER_IMPLICATIONS = 5

INTELLIGENCE_CATEGORIES = frozenset(
    {
        SignalCategory.NEWS,
        SignalCategory.COMPANY,
        SignalCategory.FUNDING,
        SignalCategory.MARKET_TREND,
    }
)

INTERPRETATION_USER_PREFERENCE_KEYS = (
    "career_status",
    "career_objectives",
    "role_preferences",
    "work_content_preferences",
    "industry_preferences",
    "location_preferences",
    "organization_preferences",
    "seniority_preferences",
    "business_model_exclusions",
    "work_environment_preferences",
    "work_style_preferences",
    "career_value_scores",
    "career_tradeoffs",
    "soft_preferences",
    "hard_constraints",
)

CAREER_SIGNAL_EVIDENCE_METADATA_KEYS = (
    "event_type",
    "signal_details",
    "source_excerpt",
)

EMPTY_INPUT_WARNING = (
    "No Intelligence signals were supplied; no interpretation was performed."
)


class CareerIntelligenceInterpretationError(Exception):
    """Raised when Career Intelligence Interpretation cannot be completed."""


class InterpretationConfidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(frozen=True)
class ThemeInterpretation:
    title: str
    summary: str
    supporting_signal_ids: tuple[str, ...]
    relevant_career_path_ids: tuple[str, ...]
    confidence: InterpretationConfidence

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "summary": self.summary,
            "supporting_signal_ids": list(self.supporting_signal_ids),
            "relevant_career_path_ids": list(self.relevant_career_path_ids),
            "confidence": self.confidence.value,
        }


@dataclass(frozen=True)
class KeyDevelopmentInterpretation:
    title: str
    summary: str
    why_it_matters: str
    supporting_signal_ids: tuple[str, ...]
    confidence: InterpretationConfidence

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "summary": self.summary,
            "why_it_matters": self.why_it_matters,
            "supporting_signal_ids": list(self.supporting_signal_ids),
            "confidence": self.confidence.value,
        }


@dataclass(frozen=True)
class CareerImplicationInterpretation:
    summary: str
    relevant_career_path_ids: tuple[str, ...]
    supporting_signal_ids: tuple[str, ...]
    confidence: InterpretationConfidence

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "relevant_career_path_ids": list(self.relevant_career_path_ids),
            "supporting_signal_ids": list(self.supporting_signal_ids),
            "confidence": self.confidence.value,
        }


@dataclass(frozen=True)
class CareerIntelligenceInterpretationResult:
    schema_version: str
    input_signal_ids: tuple[str, ...]
    themes: tuple[ThemeInterpretation, ...]
    key_developments: tuple[KeyDevelopmentInterpretation, ...]
    career_implications: tuple[CareerImplicationInterpretation, ...]
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "input_signal_ids": list(self.input_signal_ids),
            "themes": [theme.to_dict() for theme in self.themes],
            "key_developments": [
                development.to_dict()
                for development in self.key_developments
            ],
            "career_implications": [
                implication.to_dict()
                for implication in self.career_implications
            ],
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class InterpretationRequestContext:
    intelligence_signals: tuple[ScoredCareerSignal, ...] = ()
    target_career_paths: tuple[TargetCareerPath, ...] = ()
    user_preferences: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RenderedInterpretationPrompt:
    system_prompt: str
    user_prompt: str
    payload: dict[str, Any]
    canonical_example_input: dict[str, Any]
    canonical_output_example: dict[str, Any]

    @property
    def messages(self) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": self.user_prompt},
        ]


CAREER_INTELLIGENCE_INTERPRETATION_SYSTEM_PROMPT = """You are the multi-signal Career Intelligence Interpretation component of a career intelligence system.

Interpret only the supplied Intelligence CareerSignals. The supplied signals have already been filtered, normalized, scored, and routed.

Use only information supplied in this request. Do not search for or supplement factual claims using remembered knowledge about companies, industries, technologies, investment markets, hiring trends, or current events. You may synthesize and infer only from the supplied signals, and uncertainty must remain visible when evidence is indirect or incomplete.

Do not reinterpret individual job Opportunities. Do not compare jobs, assess candidate qualifications or application feasibility, recommend jobs, or create a job shortlist.

Do not recalculate, validate, change, or replace Priority Score. Priority context is authoritative upstream context only. Do not create any numeric interpretation score or replacement ranking system.

Do not redo CareerPath matching. Use only the supplied TargetCareerPath IDs, and never create, rename, infer, or rematch a CareerPath.

Theme answers:
"What broader user-relevant pattern is emerging across multiple supplied Intelligence signals?"

A Theme requires at least TWO DISTINCT supporting signals, expresses a directional interpretation, and must be materially relevant to current career directions. It is not a generic topic label, a rewrite of one signal, a CareerPath name, a user preference, or an unsupported prediction. Never generate a Theme from one isolated signal. Do not force a Theme when evidence is insufficient; the themes array may be empty. A Theme's relevant_career_path_ids array may be empty as an optional reference annotation, but this does not waive semantic career relevance.

Key Development answers:
"What concrete recent event, change, focal development, or evidence-backed potential technological breakthrough deserves attention?"

A Key Development requires at least ONE supporting signal and describes a concrete development rather than a generic topic. One concrete significant event may be sufficient. Preserve uncertainty around possible breakthroughs. Never turn a product announcement, marketing claim, single-company statement, or weak signal into a confirmed technological breakthrough without supplied evidence.

Career Implication answers:
"What could the supplied developments mean for the user's current TargetCareerPaths?"

A Career Implication requires at least ONE supporting signal and at least ONE supplied TargetCareerPath. It must express the relationship from an external development to a potential career effect. It must not merely restate UserPreferences, invent a CareerPath, evaluate application feasibility, tell the user which job to apply to, or create an action plan.

Career Implication may infer how a supplied external development is relevant to a current CareerPath, including capability relevance, domain exposure, or possible role emphasis. It must NOT introduce a new external-world factual claim merely to make the career implication stronger. Unless the supplied signals directly support the claim, do not state that hiring demand is increasing, a market is growing, startup or investment opportunities are increasing, analyst demand is rising, job openings are increasing, or investment activity is increasing.

Valid reasoning is: supplied external development -> relevance to a supplied current CareerPath.
Invalid reasoning is: supplied external development -> invented market, hiring, or investment fact -> CareerPath implication.

SUPPORTED: "These cases make AI implementation experience more relevant to AI Strategy and Digital Transformation career paths."
NOT SUPPORTED WITHOUT DIRECT EVIDENCE: "These cases prove hiring demand for AI Strategy roles is increasing."

SUPPORTED: "These signals are relevant to VC analysts assessing enterprise AI adoption."
NOT SUPPORTED WITHOUT DIRECT EVIDENCE: "These signals show growing investment opportunities in enterprise AI startups."

SUPPORTED: "Cross-sector AI implementation is relevant to Industry Research roles tracking enterprise adoption."
NOT SUPPORTED WITHOUT DIRECT EVIDENCE: "These signals prove demand for industry research analysts is rising."

CareerSignal evidence supports factual claims about the external world. UserPreferences and TargetCareerPaths establish why supplied external developments matter to the user; they are not evidence that those developments occurred.

Interpretation confidence is categorical only. Allowed values are exactly high, medium, and low.

HIGH means direct support from multiple specific, mutually consistent supplied signals.
MEDIUM means meaningful support exists, but evidence is limited, concentrated, or requires confirmation.
LOW means an early indication, indirect inference, or relatively weak current evidence.

Confidence is not a probability and is not another Priority Score.

Return between zero and 5 Themes, zero and 8 Key Developments, and zero and 5 Career Implications. Quality and evidence grounding are more important than filling sections. Empty arrays are valid when evidence is insufficient, and conclusions must never be forced merely to populate a section.

Return only valid JSON matching the exact required response contract. Do not return markdown, commentary, recommendations, action plans, next steps, or any unrequested field."""


class CareerIntelligenceInterpretationClient:
    """OpenAI-compatible client for multi-signal Career Intelligence Interpretation."""

    def __init__(
        self,
        provider: str,
        api_key: str,
        base_url: str,
        model: str,
        *,
        client: Any | None = None,
    ) -> None:
        self.provider = provider
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.client = client

        if self.client is None:
            if not self.api_key or self.api_key.startswith("your_"):
                raise CareerIntelligenceInterpretationError(
                    "LLM_API_KEY is missing. Inject a test client or configure "
                    "an OpenAI-compatible provider before live use."
                )
            self.client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
            )

    def interpret(
        self,
        context: InterpretationRequestContext,
    ) -> CareerIntelligenceInterpretationResult:
        rendered = render_career_intelligence_interpretation_request(context)
        input_signal_ids = tuple(rendered.payload["input_signal_ids"])
        target_career_path_ids = tuple(
            path["path_id"]
            for path in rendered.payload["target_career_paths"]
        )

        if not input_signal_ids:
            return CareerIntelligenceInterpretationResult(
                schema_version=CAREER_INTELLIGENCE_INTERPRETATION_SCHEMA_VERSION,
                input_signal_ids=(),
                themes=(),
                key_developments=(),
                career_implications=(),
                warnings=(EMPTY_INPUT_WARNING,),
            )

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=rendered.messages,
                response_format={"type": "json_object"},
                stream=False,
            )
        except Exception as error:
            raise CareerIntelligenceInterpretationError(
                "Career Intelligence Interpretation request failed."
            ) from error

        response_text = _extract_response_text(response)
        return parse_career_intelligence_interpretation_response(
            response_text,
            expected_input_signal_ids=input_signal_ids,
            expected_career_path_ids=target_career_path_ids,
        )


def render_career_intelligence_interpretation_request(
    context: InterpretationRequestContext,
) -> RenderedInterpretationPrompt:
    signals = _validate_interpretation_signals(context.intelligence_signals)
    target_paths = _validate_target_career_paths(context.target_career_paths)
    target_path_ids = {path.path_id for path in target_paths}

    signal_payloads = [
        _scored_intelligence_signal_payload(
            scored,
            target_career_path_ids=target_path_ids,
        )
        for scored in signals
    ]
    input_signal_ids = [
        payload["signal_id"]
        for payload in signal_payloads
    ]

    payload = {
        "input_signal_ids": input_signal_ids,
        "intelligence_signals": signal_payloads,
        "target_career_paths": [
            _target_career_path_payload(path)
            for path in target_paths
        ],
        "user_preferences": _bounded_user_preferences(
            context.user_preferences
        ),
    }
    canonical_example_input = _canonical_example_input()
    canonical_output_example = _canonical_output_example()
    user_prompt = _render_user_prompt(
        payload=payload,
        canonical_example_input=canonical_example_input,
        canonical_output_example=canonical_output_example,
    )
    return RenderedInterpretationPrompt(
        system_prompt=CAREER_INTELLIGENCE_INTERPRETATION_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        payload=payload,
        canonical_example_input=canonical_example_input,
        canonical_output_example=canonical_output_example,
    )


def parse_career_intelligence_interpretation_response(
    response_text: str,
    *,
    expected_input_signal_ids: Iterable[str],
    expected_career_path_ids: Iterable[str],
) -> CareerIntelligenceInterpretationResult:
    if not isinstance(response_text, str):
        raise CareerIntelligenceInterpretationError(
            "Career Intelligence Interpretation response must be JSON text."
        )
    try:
        payload = json.loads(
            response_text,
            parse_constant=_reject_nonstandard_json_constant,
        )
    except (json.JSONDecodeError, ValueError) as error:
        raise CareerIntelligenceInterpretationError(
            "Career Intelligence Interpretation response was not valid JSON."
        ) from error

    if not isinstance(payload, dict):
        raise CareerIntelligenceInterpretationError(
            "Career Intelligence Interpretation response must be a JSON object."
        )

    return career_intelligence_interpretation_result_from_dict(
        payload,
        expected_input_signal_ids=expected_input_signal_ids,
        expected_career_path_ids=expected_career_path_ids,
    )


def career_intelligence_interpretation_result_from_dict(
    payload: dict[str, Any],
    *,
    expected_input_signal_ids: Iterable[str],
    expected_career_path_ids: Iterable[str],
) -> CareerIntelligenceInterpretationResult:
    expected_signal_ids = _expected_id_tuple(
        expected_input_signal_ids,
        context="supplied interpretation signal IDs",
    )
    expected_path_ids = _expected_id_tuple(
        expected_career_path_ids,
        context="supplied TargetCareerPath IDs",
    )

    _require_exact_keys(
        payload,
        {
            "schema_version",
            "input_signal_ids",
            "themes",
            "key_developments",
            "career_implications",
            "warnings",
        },
        context="response",
    )

    if (
        payload["schema_version"]
        != CAREER_INTELLIGENCE_INTERPRETATION_SCHEMA_VERSION
    ):
        raise CareerIntelligenceInterpretationError(
            "Career Intelligence Interpretation schema_version is invalid."
        )

    input_signal_ids = _id_list(
        payload["input_signal_ids"],
        context="input_signal_ids",
        allowed_ids=set(expected_signal_ids),
    )
    if set(input_signal_ids) != set(expected_signal_ids):
        raise CareerIntelligenceInterpretationError(
            "Returned input_signal_ids do not exactly match supplied signals."
        )

    themes_payload = _object_list(payload["themes"], context="themes")
    developments_payload = _object_list(
        payload["key_developments"],
        context="key_developments",
    )
    implications_payload = _object_list(
        payload["career_implications"],
        context="career_implications",
    )

    if len(themes_payload) > MAX_THEMES:
        raise CareerIntelligenceInterpretationError(
            "Career Intelligence Interpretation returned too many Themes."
        )
    if len(developments_payload) > MAX_KEY_DEVELOPMENTS:
        raise CareerIntelligenceInterpretationError(
            "Career Intelligence Interpretation returned too many Key Developments."
        )
    if len(implications_payload) > MAX_CAREER_IMPLICATIONS:
        raise CareerIntelligenceInterpretationError(
            "Career Intelligence Interpretation returned too many Career Implications."
        )

    input_id_set = set(input_signal_ids)
    path_id_set = set(expected_path_ids)
    themes = tuple(
        _theme_from_payload(
            item,
            input_signal_ids=input_id_set,
            career_path_ids=path_id_set,
        )
        for item in themes_payload
    )
    key_developments = tuple(
        _key_development_from_payload(
            item,
            input_signal_ids=input_id_set,
        )
        for item in developments_payload
    )
    career_implications = tuple(
        _career_implication_from_payload(
            item,
            input_signal_ids=input_id_set,
            career_path_ids=path_id_set,
        )
        for item in implications_payload
    )
    warnings = _non_empty_string_list(
        payload["warnings"],
        context="warnings",
    )

    return CareerIntelligenceInterpretationResult(
        schema_version=CAREER_INTELLIGENCE_INTERPRETATION_SCHEMA_VERSION,
        input_signal_ids=input_signal_ids,
        themes=themes,
        key_developments=key_developments,
        career_implications=career_implications,
        warnings=warnings,
    )


def _theme_from_payload(
    payload: dict[str, Any],
    *,
    input_signal_ids: set[str],
    career_path_ids: set[str],
) -> ThemeInterpretation:
    _require_exact_keys(
        payload,
        {
            "title",
            "summary",
            "supporting_signal_ids",
            "relevant_career_path_ids",
            "confidence",
        },
        context="Theme",
    )
    supporting_signal_ids = _id_list(
        payload["supporting_signal_ids"],
        context="Theme supporting_signal_ids",
        allowed_ids=input_signal_ids,
        minimum=2,
    )
    relevant_path_ids = _id_list(
        payload["relevant_career_path_ids"],
        context="Theme relevant_career_path_ids",
        allowed_ids=career_path_ids,
    )
    return ThemeInterpretation(
        title=_non_empty_string(payload["title"], context="Theme title"),
        summary=_non_empty_string(payload["summary"], context="Theme summary"),
        supporting_signal_ids=supporting_signal_ids,
        relevant_career_path_ids=relevant_path_ids,
        confidence=_confidence(payload["confidence"], context="Theme"),
    )


def _key_development_from_payload(
    payload: dict[str, Any],
    *,
    input_signal_ids: set[str],
) -> KeyDevelopmentInterpretation:
    _require_exact_keys(
        payload,
        {
            "title",
            "summary",
            "why_it_matters",
            "supporting_signal_ids",
            "confidence",
        },
        context="Key Development",
    )
    return KeyDevelopmentInterpretation(
        title=_non_empty_string(
            payload["title"],
            context="Key Development title",
        ),
        summary=_non_empty_string(
            payload["summary"],
            context="Key Development summary",
        ),
        why_it_matters=_non_empty_string(
            payload["why_it_matters"],
            context="Key Development why_it_matters",
        ),
        supporting_signal_ids=_id_list(
            payload["supporting_signal_ids"],
            context="Key Development supporting_signal_ids",
            allowed_ids=input_signal_ids,
            minimum=1,
        ),
        confidence=_confidence(
            payload["confidence"],
            context="Key Development",
        ),
    )


def _career_implication_from_payload(
    payload: dict[str, Any],
    *,
    input_signal_ids: set[str],
    career_path_ids: set[str],
) -> CareerImplicationInterpretation:
    _require_exact_keys(
        payload,
        {
            "summary",
            "relevant_career_path_ids",
            "supporting_signal_ids",
            "confidence",
        },
        context="Career Implication",
    )
    return CareerImplicationInterpretation(
        summary=_non_empty_string(
            payload["summary"],
            context="Career Implication summary",
        ),
        relevant_career_path_ids=_id_list(
            payload["relevant_career_path_ids"],
            context="Career Implication relevant_career_path_ids",
            allowed_ids=career_path_ids,
            minimum=1,
        ),
        supporting_signal_ids=_id_list(
            payload["supporting_signal_ids"],
            context="Career Implication supporting_signal_ids",
            allowed_ids=input_signal_ids,
            minimum=1,
        ),
        confidence=_confidence(
            payload["confidence"],
            context="Career Implication",
        ),
    )


def _validate_interpretation_signals(
    values: tuple[ScoredCareerSignal, ...] | list[ScoredCareerSignal],
) -> tuple[ScoredCareerSignal, ...]:
    if not isinstance(values, (tuple, list)):
        raise CareerIntelligenceInterpretationError(
            "intelligence_signals must be a tuple or list."
        )

    signals = tuple(values)
    seen_ids: set[str] = set()
    for scored in signals:
        if not isinstance(scored, ScoredCareerSignal):
            raise CareerIntelligenceInterpretationError(
                "intelligence_signals must contain ScoredCareerSignal objects."
            )
        signal_id = scored.career_signal.signal_id
        if not isinstance(signal_id, str) or not signal_id.strip():
            raise CareerIntelligenceInterpretationError(
                "Every supplied Intelligence signal requires a non-empty signal_id."
            )
        if signal_id in seen_ids:
            raise CareerIntelligenceInterpretationError(
                "Supplied Intelligence signal IDs must be unique."
            )
        seen_ids.add(signal_id)

        category = _signal_category(scored.career_signal.category)
        if category not in INTELLIGENCE_CATEGORIES:
            raise CareerIntelligenceInterpretationError(
                "Career Intelligence Interpretation accepts only Intelligence-category signals."
            )
        if _assessment_profile(scored.assessment_profile) != AssessmentProfile.INTELLIGENCE:
            raise CareerIntelligenceInterpretationError(
                "Every supplied signal must use the Intelligence assessment profile."
            )
        if (
            _assessment_profile(scored.priority_assessment.assessment_profile)
            != AssessmentProfile.INTELLIGENCE
            or _assessment_profile(scored.priority_score.profile)
            != AssessmentProfile.INTELLIGENCE
        ):
            raise CareerIntelligenceInterpretationError(
                "Preserved assessment and score profiles must be Intelligence."
            )
        if scored.priority_assessment.signal_id != signal_id:
            raise CareerIntelligenceInterpretationError(
                "PriorityAssessmentResult signal_id must match CareerSignal.signal_id."
            )
        if scored.priority_score.signal_id != signal_id:
            raise CareerIntelligenceInterpretationError(
                "PriorityScoreResult signal_id must match CareerSignal.signal_id."
            )
    return signals


def _validate_target_career_paths(
    values: tuple[TargetCareerPath, ...] | list[TargetCareerPath],
) -> tuple[TargetCareerPath, ...]:
    if not isinstance(values, (tuple, list)):
        raise CareerIntelligenceInterpretationError(
            "target_career_paths must be a tuple or list."
        )
    paths = tuple(values)
    seen_ids: set[str] = set()
    for path in paths:
        if not isinstance(path, TargetCareerPath):
            raise CareerIntelligenceInterpretationError(
                "target_career_paths must contain TargetCareerPath objects."
            )
        if not isinstance(path.path_id, str) or not path.path_id.strip():
            raise CareerIntelligenceInterpretationError(
                "Every TargetCareerPath requires a non-empty path_id."
            )
        if path.path_id in seen_ids:
            raise CareerIntelligenceInterpretationError(
                "Supplied TargetCareerPath IDs must be unique."
            )
        seen_ids.add(path.path_id)
    return paths


def _scored_intelligence_signal_payload(
    scored: ScoredCareerSignal,
    *,
    target_career_path_ids: set[str],
) -> dict[str, Any]:
    signal = scored.career_signal
    matched_path_ids = _validated_matched_path_ids(
        scored.priority_score.matched_path_ids,
        target_career_path_ids=target_career_path_ids,
    )
    semantic_components = {}
    for name in ("career_relevance_strength", "signal_significance"):
        component = scored.priority_assessment.components.get(name)
        if component is not None:
            semantic_components[name] = component.to_dict()

    metadata = signal.metadata if isinstance(signal.metadata, dict) else {}
    source_evidence = {
        key: metadata[key]
        for key in CAREER_SIGNAL_EVIDENCE_METADATA_KEYS
        if key in metadata
    }
    payload = {
        "signal_id": signal.signal_id,
        "category": _enum_value(signal.category),
        "title": signal.title,
        "organization": signal.organization,
        "published_at": signal.published_at,
        "summary": signal.summary,
        "matched_career_path_ids": list(matched_path_ids),
        "intelligence_assessment": semantic_components,
        "priority_context": {
            "priority_score": scored.priority_score.priority_score,
            "tier": _enum_value(scored.priority_score.tier),
        },
    }
    if source_evidence:
        payload["source_evidence"] = source_evidence
    return payload


def _validated_matched_path_ids(
    values: tuple[str, ...] | list[str],
    *,
    target_career_path_ids: set[str],
) -> tuple[str, ...]:
    if not isinstance(values, (tuple, list)):
        raise CareerIntelligenceInterpretationError(
            "PriorityScoreResult matched_path_ids must be a tuple or list."
        )
    matched_ids = tuple(values)
    if any(not isinstance(value, str) or not value.strip() for value in matched_ids):
        raise CareerIntelligenceInterpretationError(
            "PriorityScoreResult matched_path_ids must be non-empty strings."
        )
    if len(set(matched_ids)) != len(matched_ids):
        raise CareerIntelligenceInterpretationError(
            "PriorityScoreResult matched_path_ids must be unique."
        )
    if any(value not in target_career_path_ids for value in matched_ids):
        raise CareerIntelligenceInterpretationError(
            "PriorityScoreResult references an unknown TargetCareerPath ID."
        )
    return matched_ids


def _target_career_path_payload(path: TargetCareerPath) -> dict[str, Any]:
    return {
        "path_id": path.path_id,
        "title": path.title,
        "category": _enum_value(path.category),
        "description": path.description,
        "suggested_roles": list(path.suggested_roles),
    }


def _bounded_user_preferences(
    user_preferences: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(user_preferences, dict):
        raise CareerIntelligenceInterpretationError(
            "user_preferences must be a JSON object."
        )
    return {
        key: user_preferences[key]
        for key in INTERPRETATION_USER_PREFERENCE_KEYS
        if key in user_preferences
    }


def _canonical_example_input() -> dict[str, Any]:
    return {
        "input_signal_ids": ["signal-001", "signal-002"],
        "target_career_path_ids": ["path-ai-strategy"],
    }


def _canonical_output_example() -> dict[str, Any]:
    return {
        "schema_version": CAREER_INTELLIGENCE_INTERPRETATION_SCHEMA_VERSION,
        "input_signal_ids": ["signal-001", "signal-002"],
        "themes": [
            {
                "title": "Enterprise AI moves toward operational implementation",
                "summary": (
                    "Multiple supplied signals indicate increasing emphasis on "
                    "operational deployment and organizational adoption of "
                    "enterprise AI."
                ),
                "supporting_signal_ids": ["signal-001", "signal-002"],
                "relevant_career_path_ids": ["path-ai-strategy"],
                "confidence": "medium",
            }
        ],
        "key_developments": [
            {
                "title": "AI expands into a concrete enterprise workflow",
                "summary": (
                    "A supplied signal describes AI moving into a specific "
                    "operational business workflow."
                ),
                "why_it_matters": (
                    "This provides concrete evidence that enterprise AI adoption "
                    "is moving beyond general experimentation."
                ),
                "supporting_signal_ids": ["signal-001"],
                "confidence": "medium",
            }
        ],
        "career_implications": [
            {
                "summary": (
                    "Implementation and organizational-transformation experience "
                    "may become increasingly relevant for AI strategy-oriented "
                    "career paths."
                ),
                "relevant_career_path_ids": ["path-ai-strategy"],
                "supporting_signal_ids": ["signal-001", "signal-002"],
                "confidence": "medium",
            }
        ],
        "warnings": [],
    }


def _render_user_prompt(
    *,
    payload: dict[str, Any],
    canonical_example_input: dict[str, Any],
    canonical_output_example: dict[str, Any],
) -> str:
    return "\n\n".join(
        (
            "INTERPRETATION INPUT\n" + _canonical_json(payload),
            "EVIDENCE BOUNDARY\n"
            "Use only the Intelligence signals in INTERPRETATION INPUT as factual "
            "evidence about the external world. UserPreferences and "
            "TargetCareerPaths establish user relevance only and must not be "
            "treated as proof that an external development occurred.",
            "RESPONSE IDENTITY AND SHAPE\n"
            "Return a JSON object with exactly these top-level keys: "
            "schema_version, input_signal_ids, themes, key_developments, "
            "career_implications, warnings. schema_version must be exactly "
            '"career_intelligence_interpretation_v1". input_signal_ids must contain '
            "exactly all supplied input signal IDs, with no duplicate, missing, "
            "additional, or invented ID. No extra top-level fields are allowed.",
            "THEME CONTRACT\n"
            "Each Theme must contain exactly title, summary, "
            "supporting_signal_ids, relevant_career_path_ids, and confidence. "
            "title and summary must be non-empty. supporting_signal_ids must "
            "contain at least TWO DISTINCT supplied input signal IDs. "
            "relevant_career_path_ids may be empty, but every listed ID must be "
            "a supplied TargetCareerPath ID. Return no more than 5 Themes. A "
            "single signal cannot support a Theme, and Themes must not be forced.",
            "KEY DEVELOPMENT CONTRACT\n"
            "Each Key Development must contain exactly title, summary, "
            "why_it_matters, supporting_signal_ids, and confidence. Required "
            "strings must be non-empty. supporting_signal_ids must contain at "
            "least ONE supplied input signal ID. Return no more than 8 Key "
            "Developments. Preserve uncertainty around potential breakthroughs.",
            "CAREER IMPLICATION CONTRACT\n"
            "Each Career Implication must contain exactly summary, "
            "relevant_career_path_ids, supporting_signal_ids, and confidence. "
            "summary must be non-empty. It requires at least ONE supplied input "
            "signal ID and at least ONE supplied TargetCareerPath ID. Return no "
            "more than 5 Career Implications. Express external development to "
            "potential career effect; do not create recommendations or actions.",
            "CONFIDENCE CONTRACT\n"
            "Every confidence must be exactly high, medium, or low. Use high for "
            "direct support from multiple specific and mutually consistent "
            "signals; medium for meaningful but limited, concentrated, or "
            "unconfirmed support; low for an early indication, indirect "
            "inference, or relatively weak current evidence. Confidence is not "
            "numeric and is not a probability or Priority Score.",
            "WARNINGS CONTRACT\n"
            "warnings must be an array of non-empty strings and may be empty. "
            "Warnings describe evidence limitations only; they are not a hidden "
            "recommendation or action-plan section.",
            "CONCRETE SYNTHETIC EXAMPLE INPUT IDENTITY\n"
            + _canonical_json(canonical_example_input),
            "CONCRETE VALID OUTPUT EXAMPLE\n"
            + _canonical_json(canonical_output_example),
            "The concrete output example is grounded only in the immediately "
            "preceding synthetic example input identity. For the actual response, "
            "use the exact signal IDs and TargetCareerPath IDs from INTERPRETATION "
            "INPUT, not the synthetic example IDs. Do not copy example objects "
            "when actual supplied evidence does not support them, and do not "
            "force any section. Return JSON only.",
        )
    )


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            indent=2,
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise CareerIntelligenceInterpretationError(
            "Interpretation request context is not JSON serializable."
        ) from error


def _extract_response_text(response: Any) -> str:
    try:
        response_text = response.choices[0].message.content
    except Exception as error:
        raise CareerIntelligenceInterpretationError(
            "Career Intelligence Interpretation response shape is invalid."
        ) from error

    if not isinstance(response_text, str) or not response_text.strip():
        raise CareerIntelligenceInterpretationError(
            "Career Intelligence Interpretation returned an empty response."
        )
    return response_text


def _reject_nonstandard_json_constant(value: str) -> Any:
    raise ValueError(f"Non-standard JSON constant is not allowed: {value}.")


def _require_exact_keys(
    payload: dict[str, Any],
    required_keys: set[str],
    *,
    context: str,
) -> None:
    if set(payload) != required_keys:
        raise CareerIntelligenceInterpretationError(
            f"Career Intelligence Interpretation {context} fields do not match the contract."
        )


def _object_list(value: Any, *, context: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(
        not isinstance(item, dict)
        for item in value
    ):
        raise CareerIntelligenceInterpretationError(
            f"Career Intelligence Interpretation {context} must be an array of objects."
        )
    return value


def _non_empty_string(value: Any, *, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CareerIntelligenceInterpretationError(
            f"Career Intelligence Interpretation {context} must be a non-empty string."
        )
    return value


def _non_empty_string_list(value: Any, *, context: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise CareerIntelligenceInterpretationError(
            f"Career Intelligence Interpretation {context} must be an array."
        )
    strings = tuple(
        _non_empty_string(item, context=f"{context} item")
        for item in value
    )
    return strings


def _id_list(
    value: Any,
    *,
    context: str,
    allowed_ids: set[str],
    minimum: int = 0,
) -> tuple[str, ...]:
    ids = _non_empty_string_list(value, context=context)
    if len(ids) < minimum:
        raise CareerIntelligenceInterpretationError(
            f"Career Intelligence Interpretation {context} contains too few IDs."
        )
    if len(set(ids)) != len(ids):
        raise CareerIntelligenceInterpretationError(
            f"Career Intelligence Interpretation {context} contains duplicate IDs."
        )
    if any(value not in allowed_ids for value in ids):
        raise CareerIntelligenceInterpretationError(
            f"Career Intelligence Interpretation {context} contains an unknown ID."
        )
    return ids


def _expected_id_tuple(values: Iterable[str], *, context: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise CareerIntelligenceInterpretationError(
            f"{context} must be a collection of IDs."
        )
    try:
        ids = tuple(values)
    except TypeError as error:
        raise CareerIntelligenceInterpretationError(
            f"{context} must be a collection of IDs."
        ) from error
    if any(not isinstance(value, str) or not value.strip() for value in ids):
        raise CareerIntelligenceInterpretationError(
            f"{context} must contain non-empty strings."
        )
    if len(set(ids)) != len(ids):
        raise CareerIntelligenceInterpretationError(
            f"{context} must contain unique IDs."
        )
    return ids


def _confidence(value: Any, *, context: str) -> InterpretationConfidence:
    if not isinstance(value, str):
        raise CareerIntelligenceInterpretationError(
            f"Career Intelligence Interpretation {context} confidence is invalid."
        )
    try:
        return InterpretationConfidence(value)
    except ValueError as error:
        raise CareerIntelligenceInterpretationError(
            f"Career Intelligence Interpretation {context} confidence is invalid."
        ) from error


def _signal_category(value: SignalCategory | str | Any) -> SignalCategory | None:
    if isinstance(value, SignalCategory):
        return value
    try:
        return SignalCategory(str(value))
    except ValueError:
        return None


def _assessment_profile(value: AssessmentProfile | str | Any) -> AssessmentProfile | None:
    if isinstance(value, AssessmentProfile):
        return value
    try:
        return AssessmentProfile(str(value))
    except ValueError:
        return None


def _enum_value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value
