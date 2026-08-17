import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from openai import OpenAI

from src.models import CareerSignal, RawItem, TargetCareerPath, UserProfile


PRIORITY_ASSESSMENT_SCHEMA_VERSION = "priority_assessment_v1"

ALLOWED_SEMANTIC_SCORES = {0.0, 0.25, 0.5, 0.75, 1.0}

OPPORTUNITY_COMPONENTS = ("user_policy_fit", "opportunity_feasibility")
INTELLIGENCE_COMPONENTS = (
    "career_relevance_strength",
    "signal_significance",
)

OPPORTUNITY_USER_PREFERENCE_KEYS = (
    "hard_constraints",
    "soft_preferences",
    "location_preferences",
    "seniority_preferences",
    "experience_requirement_tolerance",
    "career_status",
    "career_objectives",
    "role_preferences",
    "work_content_preferences",
    "industry_preferences",
    "business_model_exclusions",
    "work_authorization",
    "organization_preferences",
    "work_environment_preferences",
    "work_style_preferences",
    "compensation_preferences",
    "career_value_scores",
    "career_tradeoffs",
)

INTELLIGENCE_USER_PREFERENCE_KEYS = (
    "hard_constraints",
    "soft_preferences",
    "location_preferences",
    "seniority_preferences",
    "career_status",
    "career_objectives",
    "role_preferences",
    "work_content_preferences",
    "industry_preferences",
    "business_model_exclusions",
    "organization_preferences",
    "career_value_scores",
    "career_tradeoffs",
)

CAREER_SIGNAL_SEMANTIC_METADATA_KEYS = (
    "location",
    "locations",
    "seniority",
    "employment_type",
    "experience_requirements",
    "qualifications",
    "responsibilities",
    "compensation",
    "event_type",
    "signal_details",
    "source_excerpt",
)

SOURCE_EVIDENCE_METADATA_KEYS = (
    "location",
    "locations",
    "seniority",
    "employment_type",
    "experience_requirements",
    "qualifications",
    "responsibilities",
    "compensation",
    "event_type",
    "signal_details",
    "source_excerpt",
    "position",
    "feed_name",
    "website_name",
)


class PriorityAssessmentError(Exception):
    """
    Raised when priority semantic assessment cannot be completed.
    """


class AssessmentProfile(str, Enum):
    OPPORTUNITY = "opportunity"
    INTELLIGENCE = "intelligence"


class ComponentStatus(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class SemanticComponentResult:
    status: ComponentStatus
    score: float | None
    reason: str
    evidence: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "score": self.score,
            "reason": self.reason,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class PriorityAssessmentResult:
    schema_version: str
    signal_id: str
    assessment_profile: AssessmentProfile
    components: dict[str, SemanticComponentResult]
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "signal_id": self.signal_id,
            "assessment_profile": self.assessment_profile.value,
            "components": {
                key: component.to_dict()
                for key, component in self.components.items()
            },
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class PriorityAssessmentInput:
    assessment_profile: AssessmentProfile
    signal_id: str
    as_of: str
    career_signal: CareerSignal
    matched_career_path_ids: tuple[str, ...] = ()
    target_career_paths: tuple[TargetCareerPath, ...] = ()
    user_preferences: dict[str, Any] = field(default_factory=dict)
    user_profile: UserProfile | None = None
    supporting_source_evidence: RawItem | dict[str, Any] | None = None


@dataclass(frozen=True)
class RenderedPriorityAssessmentRequest:
    system_prompt: str
    user_prompt: str
    payload: dict[str, Any]
    warnings: tuple[str, ...]

    @property
    def messages(self) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": self.user_prompt},
        ]


OPPORTUNITY_SYSTEM_PROMPT = """You are the single-signal opportunity assessment component of a career intelligence system.

The item has already passed an upstream relevance filter.

Your task is NOT to decide whether the item should be kept.
Your task is NOT to calculate the final Priority Score.
Your task is NOT to redo CareerPath matching.

Evaluate ONLY these semantic dimensions:

1. user_policy_fit
2. opportunity_feasibility

Use only the information supplied in this request.

Do not use unsupported outside assumptions about the company, role, culture, compensation, seniority, hiring process, qualifications, or location.

Missing evidence is not negative evidence.

If the supplied evidence is insufficient for a dimension, return that dimension as unavailable.

Allowed numeric scores are ONLY:

0.00
0.25
0.50
0.75
1.00

Do not produce intermediate values.

Every available score must include a concise reason and specific supporting evidence from the supplied information.

Do not produce an overall score or recommendation.

Return only valid JSON matching the required schema."""


INTELLIGENCE_SYSTEM_PROMPT = """You are the single-signal intelligence assessment component of a career intelligence system.

The item has already passed an upstream relevance filter.

Your task is NOT to decide whether the item should be kept.
Your task is NOT to calculate the final Priority Score.
Your task is NOT to redo CareerPath matching.

You are evaluating ONE CareerSignal only.

Evaluate ONLY these semantic dimensions:

1. career_relevance_strength
2. signal_significance

Use only the information supplied in this request.

Do not use unsupported outside assumptions.

Do not infer a broader market, hiring, industry, investment, or career trend from this single signal.

Cross-signal trend synthesis belongs to a separate downstream interpretation stage.

Missing evidence is not negative evidence.

If the evidence is insufficient for a dimension, return that dimension as unavailable.

Allowed numeric scores are ONLY:

0.00
0.25
0.50
0.75
1.00

Do not produce intermediate values.

Every available score must include a concise reason and specific supporting evidence.

Do not produce an overall score or recommendation.

Return only valid JSON matching the required schema."""


OPPORTUNITY_RUBRIC = """USER POLICY FIT:

Definition:

How well the actual characteristics of the opportunity align with the user's explicitly stated career preferences, positive preferences, exclusions, and constraints.

This dimension may consider supplied evidence related to:

- work content;
- industry;
- business model;
- role characteristics;
- seniority preference;
- location preference;
- work-environment preference;
- explicit user exclusions;
- other relevant UserPreferences.

This dimension does NOT evaluate:

- whether the user is qualified;
- CareerPath priority;
- recency;
- source quality;
- company prestige based on outside knowledge.

Rubric:

1.00 - Very Strong Fit
Clear evidence of strong alignment with multiple important positive preferences and no material evidenced conflict.

0.75 - Strong Fit
Overall clearly aligned with user preferences, with only minor concerns, incomplete secondary information, or limited uncertainty.

0.50 - Mixed Fit
Meaningful positive alignment exists, but there is also meaningful evidenced conflict or tension.

Uncertainty alone must NOT produce 0.50.

0.25 - Weak Fit / Material Conflict
Career relevance remains, but multiple important user-preference conflicts exist, or one substantial evidenced conflict materially reduces fit.

0.00 - Direct Policy Conflict
Clear opportunity evidence directly conflicts with an explicit user hard preference or exclusion.

This score affects the normal weighted scoring calculation.

It does NOT trigger a separate final-score override or cap.

unavailable
The supplied opportunity evidence is insufficient to make a defensible user-policy judgment.

OPPORTUNITY FEASIBILITY:

Definition:

How realistically attainable this opportunity is for the user based on explicit opportunity requirements and supplied user background.

Possible evidence may include:

- years of experience;
- seniority;
- required skills;
- education;
- qualifications;
- eligibility requirements;
- work authorization;
- explicit location requirements.

This dimension evaluates reasonable application feasibility.

It does NOT estimate:

- probability of receiving an offer;
- user preference;
- career-path importance;
- company prestige;
- long-term career upside.

Rubric:

1.00 - Clearly Attainable
Evidence places the opportunity clearly within the user's normal competitive range, with no material qualification gap.

0.75 - Attainable With Manageable Gaps
Broadly realistic, with minor or manageable gaps.

0.50 - Meaningful Stretch but Plausible
The opportunity represents a meaningful stretch but remains realistically contestable.

The user's existing experience-tolerance policy may be reflected when explicit role experience evidence exists.

0.25 - Major Gap
Explicit evidence shows substantial qualification, experience, seniority, or eligibility gaps.

The opportunity may technically remain possible, but current feasibility is low.

0.00 - Explicitly Implausible / Ineligible
Clear supplied evidence establishes major incompatibility or explicit ineligibility.

unavailable
Opportunity requirements are insufficiently stated.

Job title alone is not sufficient evidence for qualification requirements.

Additional rules:

- Do not estimate offer probability.
- Do not infer requirements from title alone.
- Do not guess missing years of experience.
- Do not guess location requirements.
- Do not guess company culture.
- Do not guess compensation.
- Do not calculate priority_score."""


INTELLIGENCE_RUBRIC = """CAREER RELEVANCE STRENGTH:

Definition:

How directly and materially the specific development matters to the user's current career directions.

This differs from Path Alignment.

Path Alignment answers:

"Which TargetCareerPath does this signal relate to, and how strong is that path for the user?"

Career Relevance Strength answers:

"How much does this specific event actually matter for that career direction?"

Relevant evidence may concern:

- hiring demand;
- role creation;
- capability expansion;
- organizational strategy;
- investment direction;
- market demand;
- business adoption;
- talent structure;
- other concrete career-market implications.

Rubric:

1.00 - Direct and Material Career Relevance
The development directly and materially affects one or more important current career directions.

0.75 - Clear Career Relevance
The development has clear and meaningful career implications, although the connection is less direct or less consequential.

0.50 - Indirect but Useful
A defensible career implication exists but requires an additional reasoning step.

0.25 - Peripheral
The topic overlaps with the user's career interests, but the specific event has weak practical career meaning.

0.00 - No Material Career Meaning
Further assessment finds essentially no substantive career value, despite the item having passed the broader upstream filter.

unavailable
Evidence is insufficient to determine career meaning.

SIGNAL SIGNIFICANCE:

Definition:

How substantive, concrete, and information-rich this individual signal is, independent of the user's personal fit.

This dimension evaluates the signal itself.

It does NOT evaluate whether the user personally cares about it.

Rubric:

1.00 - Major Concrete Development
A clear and substantial event or change, such as:

- major expansion;
- major investment;
- significant funding;
- acquisition;
- new business unit;
- major strategic shift;
- major hiring initiative;
- substantial organizational restructuring;
- major new capability/practice.

0.75 - Specific Meaningful Development
A concrete, meaningful development with real informational value, but below the highest level of materiality.

0.50 - Moderate / Incremental Signal
Real and useful information representing a moderate or incremental development.

0.25 - Weak / Generic Signal
Mostly generic commentary, promotional content, ordinary thought leadership, vague optimism, or low-information material.

0.00 - Essentially No Intelligence Value
The item provides essentially no substantive intelligence evidence.

unavailable
The supplied content is insufficient to determine what actually occurred.

Important boundary:

This dimension evaluates ONE signal only.

It must NOT infer broader trends from multiple signals.

Cross-signal synthesis belongs to downstream LLM Interpretation.

Additional rules:

- Evaluate only this signal.
- Do not synthesize across signals.
- Do not claim a broader trend unless explicitly stated by the supplied source itself.
- Do not calculate priority_score."""


class PriorityAssessmentClient:
    """
    OpenAI-compatible client for single-CareerSignal semantic assessment.
    """

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
                raise PriorityAssessmentError(
                    "LLM_API_KEY is missing. Inject a test client or configure "
                    "an OpenAI-compatible provider before live use."
                )

            self.client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
            )

    def assess(
        self,
        assessment_input: PriorityAssessmentInput,
    ) -> PriorityAssessmentResult:
        rendered = render_priority_assessment_request(assessment_input)

        response = self.client.chat.completions.create(
            model=self.model,
            messages=rendered.messages,
            response_format={"type": "json_object"},
            stream=False,
        )

        try:
            result = _parse_priority_assessment_client_response(
                response,
                assessment_input=assessment_input,
            )
        except PriorityAssessmentError as validation_error:
            retry_response = self.client.chat.completions.create(
                model=self.model,
                messages=_corrective_retry_messages(
                    rendered,
                    validation_error=validation_error,
                ),
                response_format={"type": "json_object"},
                stream=False,
            )
            try:
                result = _parse_priority_assessment_client_response(
                    retry_response,
                    assessment_input=assessment_input,
                )
            except PriorityAssessmentError as retry_error:
                raise retry_error from validation_error
        return _with_request_warnings(result, rendered.warnings)

    def assess_opportunity(
        self,
        assessment_input: PriorityAssessmentInput,
    ) -> PriorityAssessmentResult:
        return self.assess(
            _replace_profile(assessment_input, AssessmentProfile.OPPORTUNITY)
        )

    def assess_intelligence(
        self,
        assessment_input: PriorityAssessmentInput,
    ) -> PriorityAssessmentResult:
        return self.assess(
            _replace_profile(assessment_input, AssessmentProfile.INTELLIGENCE)
        )


def render_priority_assessment_request(
    assessment_input: PriorityAssessmentInput,
) -> RenderedPriorityAssessmentRequest:
    profile = _assessment_profile(assessment_input.assessment_profile)
    if assessment_input.signal_id != assessment_input.career_signal.signal_id:
        raise PriorityAssessmentError(
            "PriorityAssessmentInput signal_id must match CareerSignal.signal_id."
        )

    matched_paths, warnings = _matched_target_career_path_payloads(
        matched_career_path_ids=assessment_input.matched_career_path_ids,
        target_career_paths=assessment_input.target_career_paths,
    )

    payload: dict[str, Any] = {
        "assessment_profile": profile.value,
        "signal_id": assessment_input.signal_id,
        "career_signal": _career_signal_semantic_payload(
            assessment_input.career_signal
        ),
        "matched_target_career_paths": matched_paths,
    }

    source_evidence = _supporting_source_evidence_payload(
        assessment_input.supporting_source_evidence
    )
    if source_evidence:
        payload["supporting_source_evidence"] = source_evidence

    if profile == AssessmentProfile.OPPORTUNITY:
        if assessment_input.user_profile is None:
            raise PriorityAssessmentError(
                "Opportunity assessment requires UserProfile context."
            )
        payload["user_preferences"] = _user_preferences_payload(
            assessment_input.user_preferences,
            OPPORTUNITY_USER_PREFERENCE_KEYS,
        )
        payload["user_profile"] = _user_profile_payload(
            assessment_input.user_profile
        )
        system_prompt = OPPORTUNITY_SYSTEM_PROMPT
        user_prompt = _render_user_prompt(
            assessment_profile=profile,
            signal_id=assessment_input.signal_id,
            sections=(
                ("USER PROFILE", payload["user_profile"]),
                ("USER PREFERENCES", payload["user_preferences"]),
                ("MATCHED TARGET CAREER PATHS", matched_paths),
                ("CAREER SIGNAL", payload["career_signal"]),
                ("SUPPORTING SOURCE EVIDENCE", source_evidence or {}),
            ),
            rubric=OPPORTUNITY_RUBRIC,
        )
    else:
        payload["user_preferences"] = _user_preferences_payload(
            assessment_input.user_preferences,
            INTELLIGENCE_USER_PREFERENCE_KEYS,
        )
        system_prompt = INTELLIGENCE_SYSTEM_PROMPT
        user_prompt = _render_user_prompt(
            assessment_profile=profile,
            signal_id=assessment_input.signal_id,
            sections=(
                ("USER PREFERENCES", payload["user_preferences"]),
                ("MATCHED TARGET CAREER PATHS", matched_paths),
                ("CAREER SIGNAL", payload["career_signal"]),
                ("SUPPORTING SOURCE EVIDENCE", source_evidence or {}),
            ),
            rubric=INTELLIGENCE_RUBRIC,
        )

    return RenderedPriorityAssessmentRequest(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        payload=payload,
        warnings=tuple(warnings),
    )


def parse_priority_assessment_response(
    response_text: str,
    *,
    expected_signal_id: str,
    expected_profile: AssessmentProfile | str,
) -> PriorityAssessmentResult:
    try:
        payload = json.loads(response_text)
    except json.JSONDecodeError as error:
        raise PriorityAssessmentError(
            "Priority Assessment response was not valid JSON."
        ) from error

    if not isinstance(payload, dict):
        raise PriorityAssessmentError(
            "Priority Assessment response must be a JSON object."
        )

    return priority_assessment_result_from_dict(
        payload,
        expected_signal_id=expected_signal_id,
        expected_profile=expected_profile,
    )


def priority_assessment_result_from_dict(
    payload: dict[str, Any],
    *,
    expected_signal_id: str,
    expected_profile: AssessmentProfile | str,
) -> PriorityAssessmentResult:
    expected = _assessment_profile(expected_profile)

    allowed_keys = {
        "schema_version",
        "signal_id",
        "assessment_profile",
        "components",
        "warnings",
    }
    unexpected = set(payload) - allowed_keys
    if unexpected:
        raise PriorityAssessmentError(
            "Priority Assessment response contained unexpected top-level "
            f"fields: {sorted(unexpected)}."
        )

    if payload.get("schema_version") != PRIORITY_ASSESSMENT_SCHEMA_VERSION:
        raise PriorityAssessmentError(
            "Priority Assessment response schema_version is invalid."
        )

    signal_id = payload.get("signal_id")
    if signal_id != expected_signal_id:
        raise PriorityAssessmentError(
            "Priority Assessment response signal_id does not match request."
        )

    profile = _assessment_profile(payload.get("assessment_profile"))
    if profile != expected:
        raise PriorityAssessmentError(
            "Priority Assessment response assessment_profile does not match "
            "request."
        )

    raw_components = payload.get("components")
    if not isinstance(raw_components, dict):
        raise PriorityAssessmentError(
            "Priority Assessment response components must be an object."
        )

    required_components = _required_component_names(profile)
    component_keys = tuple(raw_components)
    if set(component_keys) != set(required_components):
        raise PriorityAssessmentError(
            "Priority Assessment response components do not match profile: "
            f"expected={sorted(required_components)}, actual={sorted(component_keys)}."
        )

    components = {
        key: _component_from_payload(key, raw_components[key])
        for key in required_components
    }

    warnings = payload.get("warnings")
    if not isinstance(warnings, list) or any(
        not isinstance(item, str) for item in warnings
    ):
        raise PriorityAssessmentError(
            "Priority Assessment response warnings must be a list of strings."
        )

    return PriorityAssessmentResult(
        schema_version=PRIORITY_ASSESSMENT_SCHEMA_VERSION,
        signal_id=str(signal_id),
        assessment_profile=profile,
        components=components,
        warnings=tuple(warnings),
    )


def _component_from_payload(
    component_name: str,
    payload: Any,
) -> SemanticComponentResult:
    if not isinstance(payload, dict):
        raise PriorityAssessmentError(
            f"Component {component_name!r} must be an object."
        )

    allowed_keys = {"status", "score", "reason", "evidence"}
    unexpected = set(payload) - allowed_keys
    if unexpected:
        raise PriorityAssessmentError(
            f"Component {component_name!r} contained unexpected fields: "
            f"{sorted(unexpected)}."
        )

    try:
        status = ComponentStatus(str(payload.get("status")))
    except ValueError as error:
        raise PriorityAssessmentError(
            f"Component {component_name!r} has invalid status."
        ) from error

    reason = payload.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise PriorityAssessmentError(
            f"Component {component_name!r} requires a non-empty reason."
        )

    evidence = payload.get("evidence")
    if not isinstance(evidence, list) or any(
        not isinstance(item, str) or not item.strip()
        for item in evidence
    ):
        raise PriorityAssessmentError(
            f"Component {component_name!r} evidence must be a list of "
            "non-empty strings."
        )

    score = payload.get("score")
    if status == ComponentStatus.AVAILABLE:
        if not _is_allowed_semantic_score(score):
            raise PriorityAssessmentError(
                f"Component {component_name!r} has invalid available score."
            )
        if not evidence:
            raise PriorityAssessmentError(
                f"Component {component_name!r} requires evidence when available."
            )
        normalized_score = float(score)
    else:
        if score is not None:
            raise PriorityAssessmentError(
                f"Component {component_name!r} must use null score when "
                "unavailable."
            )
        if evidence:
            raise PriorityAssessmentError(
                f"Component {component_name!r} must not include evidence when "
                "unavailable."
            )
        normalized_score = None

    return SemanticComponentResult(
        status=status,
        score=normalized_score,
        reason=reason.strip(),
        evidence=tuple(evidence),
    )


def _assessment_profile(value: AssessmentProfile | str | Any) -> AssessmentProfile:
    if isinstance(value, AssessmentProfile):
        return value
    try:
        return AssessmentProfile(str(value))
    except ValueError as error:
        raise PriorityAssessmentError(
            f"Unknown assessment_profile: {value!r}."
        ) from error


def _required_component_names(profile: AssessmentProfile) -> tuple[str, ...]:
    if profile == AssessmentProfile.OPPORTUNITY:
        return OPPORTUNITY_COMPONENTS
    return INTELLIGENCE_COMPONENTS


def _is_allowed_semantic_score(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return float(value) in ALLOWED_SEMANTIC_SCORES


def _career_signal_semantic_payload(signal: CareerSignal) -> dict[str, Any]:
    return {
        "signal_id": signal.signal_id,
        "title": signal.title,
        "organization": signal.organization,
        "url": signal.url,
        "category": _enum_value(signal.category),
        "summary": signal.summary,
        "published_at": signal.published_at,
        "metadata": _selected_metadata(
            signal.metadata,
            CAREER_SIGNAL_SEMANTIC_METADATA_KEYS,
        ),
    }


def _matched_target_career_path_payloads(
    *,
    matched_career_path_ids: tuple[str, ...],
    target_career_paths: tuple[TargetCareerPath, ...],
) -> tuple[list[dict[str, Any]], list[str]]:
    paths_by_id = {
        path.path_id: path
        for path in target_career_paths
    }
    payloads: list[dict[str, Any]] = []
    warnings: list[str] = []
    seen: set[str] = set()

    for path_id in matched_career_path_ids:
        if path_id in seen:
            continue
        seen.add(path_id)
        path = paths_by_id.get(path_id)
        if path is None:
            warnings.append(f"unresolved_matched_career_path_id:{path_id}")
            continue
        payloads.append(_target_career_path_payload(path))

    return payloads, warnings


def _target_career_path_payload(path: TargetCareerPath) -> dict[str, Any]:
    metadata = path.metadata if isinstance(path.metadata, dict) else {}
    return {
        "path_id": path.path_id,
        "title": path.title,
        "description": path.description,
        "fit_score": path.fit_score,
        "path_type": metadata.get("path_type"),
        "risk_flags": _string_list(metadata.get("risk_flags")),
        "constraint_notes": _string_list(metadata.get("constraint_notes")),
        "why_not_higher": _string_list(metadata.get("why_not_higher")),
    }


def _user_profile_payload(user_profile: UserProfile) -> dict[str, Any]:
    return {
        "background_summary": user_profile.background_summary,
        "skills": list(user_profile.skills),
        "education": list(user_profile.education),
        "work_experience": list(user_profile.work_experience),
        "preferred_roles": list(user_profile.preferred_roles),
        "preferred_locations": list(user_profile.preferred_locations),
        "constraints": list(user_profile.constraints),
    }


def _user_preferences_payload(
    user_preferences: dict[str, Any],
    allowed_keys: tuple[str, ...],
) -> dict[str, Any]:
    if not isinstance(user_preferences, dict):
        raise PriorityAssessmentError("user_preferences must be a JSON object.")
    return {
        key: user_preferences[key]
        for key in allowed_keys
        if key in user_preferences
    }


def _supporting_source_evidence_payload(
    source_evidence: RawItem | dict[str, Any] | None,
) -> dict[str, Any]:
    if source_evidence is None:
        return {}

    if isinstance(source_evidence, RawItem):
        metadata = source_evidence.metadata
        return {
            "title": source_evidence.title,
            "organization": source_evidence.organization,
            "url": source_evidence.url,
            "published_at": source_evidence.published_at,
            "raw_text": source_evidence.raw_text,
            "metadata": _selected_metadata(
                metadata,
                SOURCE_EVIDENCE_METADATA_KEYS,
            ),
        }

    if not isinstance(source_evidence, dict):
        raise PriorityAssessmentError(
            "supporting_source_evidence must be a RawItem, JSON object, or None."
        )

    return {
        "title": _string_or_empty(source_evidence.get("title")),
        "organization": _string_or_empty(source_evidence.get("organization")),
        "url": _string_or_empty(source_evidence.get("url")),
        "published_at": source_evidence.get("published_at"),
        "raw_text": _string_or_empty(source_evidence.get("raw_text")),
        "metadata": _selected_metadata(
            _dict_or_empty(source_evidence.get("metadata")),
            SOURCE_EVIDENCE_METADATA_KEYS,
        ),
    }


def _selected_metadata(
    metadata: dict[str, Any],
    allowed_keys: tuple[str, ...],
) -> dict[str, Any]:
    if not isinstance(metadata, dict):
        return {}
    return {
        key: metadata[key]
        for key in allowed_keys
        if key in metadata
    }


def _render_user_prompt(
    *,
    assessment_profile: AssessmentProfile,
    signal_id: str,
    sections: tuple[tuple[str, Any], ...],
    rubric: str,
) -> str:
    rendered_sections = []
    for title, value in sections:
        rendered_sections.append(
            f"{title}\n{_canonical_json(value)}"
        )

    rendered_sections.append("RUBRIC\n" + rubric)
    rendered_sections.append(
        "REQUIRED OUTPUT JSON SKELETON\n"
        + _canonical_json(
            _output_schema_skeleton(
                assessment_profile=assessment_profile,
                signal_id=signal_id,
            )
        )
    )
    rendered_sections.append(
        "VALID COMPONENT FORM WHEN AVAILABLE\n"
        + _canonical_json(_available_component_skeleton())
    )
    rendered_sections.append(_available_score_type_instruction())
    rendered_sections.append(
        "VALID COMPONENT FORM WHEN UNAVAILABLE\n"
        + _canonical_json(_unavailable_component_skeleton())
    )
    rendered_sections.append(
        "Return JSON with schema_version, signal_id, assessment_profile, "
        "components, and warnings. Do not include priority_score, "
        "overall_score, recommendation, or any unrequested component."
    )
    return "\n\n".join(rendered_sections)


def _output_schema_skeleton(
    *,
    assessment_profile: AssessmentProfile,
    signal_id: str,
) -> dict[str, Any]:
    return {
        "schema_version": PRIORITY_ASSESSMENT_SCHEMA_VERSION,
        "signal_id": signal_id,
        "assessment_profile": assessment_profile.value,
        "components": {
            component_name: _available_component_skeleton()
            for component_name in _required_component_names(assessment_profile)
        },
        "warnings": [],
    }


def _available_component_skeleton() -> dict[str, Any]:
    return {
        "status": "available",
        "score": 0.75,
        "reason": "non-empty concise reason",
        "evidence": ["specific supplied evidence"],
    }


def _available_score_type_instruction() -> str:
    return (
        "AVAILABLE SCORE TYPE REQUIREMENT\n"
        "Allowed score values are exactly: 0, 0.25, 0.5, 0.75, 1.\n"
        "The score MUST be a JSON NUMBER.\n"
        "Never return string score values such as \"0\", \"0.25\", "
        "\"0.50\", \"0.75\", \"1\", \"1.00\", or any other string "
        "representation."
    )


def _unavailable_component_skeleton() -> dict[str, Any]:
    return {
        "status": "unavailable",
        "score": None,
        "reason": "non-empty explanation of why supplied evidence is insufficient",
        "evidence": [],
    }


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        default=_json_default,
    )


def _json_default(value: Any) -> Any:
    if hasattr(value, "value"):
        return value.value
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    raise TypeError(f"Object is not JSON serializable: {type(value).__name__}")


def _extract_response_text(response: Any) -> str:
    try:
        response_text = response.choices[0].message.content
    except Exception as error:
        raise PriorityAssessmentError(
            "Priority Assessment response shape is invalid."
        ) from error

    if response_text is None:
        raise PriorityAssessmentError("Priority Assessment returned empty response.")
    return str(response_text)


def _parse_priority_assessment_client_response(
    response: Any,
    *,
    assessment_input: PriorityAssessmentInput,
) -> PriorityAssessmentResult:
    return parse_priority_assessment_response(
        _extract_response_text(response),
        expected_signal_id=assessment_input.signal_id,
        expected_profile=assessment_input.assessment_profile,
    )


def _corrective_retry_messages(
    rendered: RenderedPriorityAssessmentRequest,
    *,
    validation_error: PriorityAssessmentError,
) -> list[dict[str, str]]:
    messages = rendered.messages
    messages[-1] = {
        "role": "user",
        "content": (
            messages[-1]["content"]
            + "\n\nCORRECTIVE RETRY INSTRUCTION\n"
            + _corrective_retry_instruction(validation_error)
        ),
    }
    return messages


def _corrective_retry_instruction(
    validation_error: PriorityAssessmentError,
) -> str:
    return (
        "The previous response failed strict Priority Assessment output "
        "validation.\n\n"
        f"Validation error:\n{validation_error}\n\n"
        "Regenerate the COMPLETE response from the original supplied context.\n"
        "Do not patch or discuss the previous JSON.\n"
        "Return a complete new JSON object matching the exact frozen schema.\n\n"
        "Remember:\n"
        "AVAILABLE -> status = \"available\"; score = one JSON number from "
        "exactly {0, 0.25, 0.5, 0.75, 1}; reason = non-empty; evidence = "
        "non-empty.\n"
        "UNAVAILABLE -> status = \"unavailable\"; score = null; reason = "
        "non-empty; evidence = [].\n\n"
        "Return JSON only."
    )


def _with_request_warnings(
    result: PriorityAssessmentResult,
    request_warnings: tuple[str, ...],
) -> PriorityAssessmentResult:
    if not request_warnings:
        return result
    return PriorityAssessmentResult(
        schema_version=result.schema_version,
        signal_id=result.signal_id,
        assessment_profile=result.assessment_profile,
        components=result.components,
        warnings=tuple(dict.fromkeys((*result.warnings, *request_warnings))),
    )


def _replace_profile(
    assessment_input: PriorityAssessmentInput,
    profile: AssessmentProfile,
) -> PriorityAssessmentInput:
    return PriorityAssessmentInput(
        assessment_profile=profile,
        signal_id=assessment_input.signal_id,
        as_of=assessment_input.as_of,
        career_signal=assessment_input.career_signal,
        matched_career_path_ids=assessment_input.matched_career_path_ids,
        target_career_paths=assessment_input.target_career_paths,
        user_preferences=assessment_input.user_preferences,
        user_profile=assessment_input.user_profile,
        supporting_source_evidence=assessment_input.supporting_source_evidence,
    )


def _enum_value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def _string_or_empty(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _dict_or_empty(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        str(item)
        for item in value
        if item is not None
    ]
