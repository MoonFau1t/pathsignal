from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
from typing import Any


MONITORING_OBJECTIVE_SCHEMA_VERSION = "monitoring_objective_v1"
INFORMATION_NEED_SCHEMA_VERSION = "information_need_v1"
INFORMATION_NEED_GENERATION_SCHEMA_VERSION = "information_need_generation_v1"
ENTITY_TYPE_DEFINITION_SCHEMA_VERSION = "entity_type_definition_v1"
ENTITY_TYPE_CANDIDATE_SCHEMA_VERSION = "entity_type_candidate_v1"
PROPOSED_ENTITY_TYPE_SCHEMA_VERSION = "proposed_entity_type_v1"
ENTITY_TYPE_EXPANSION_SCHEMA_VERSION = "entity_type_expansion_v1"


class MonitoringObjectiveCode(str, Enum):
    """
    Fixed system-defined monitoring objective taxonomy.
    """

    OPPORTUNITY = "opportunity"
    ORGANIZATION = "organization"
    INDUSTRY = "industry"
    CAREER_PATH = "career_path"


class InformationNeedPriority(str, Enum):
    """
    Controlled priority for low-frequency source-monitoring planning.
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(frozen=True)
class MonitoringObjectiveDefinition:
    code: MonitoringObjectiveCode
    label: str
    description: str
    supported_signal_examples: tuple[str, ...]
    schema_version: str = MONITORING_OBJECTIVE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)


@dataclass(frozen=True)
class LLMExecutionMetadata:
    provider: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    schema_version: str = INFORMATION_NEED_SCHEMA_VERSION
    input_fingerprint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)


@dataclass(frozen=True)
class InformationNeed:
    information_need_id: str
    need_key: str
    objective_code: MonitoringObjectiveCode
    title: str
    description: str
    related_target_career_path_ids: tuple[str, ...]
    signal_examples: tuple[str, ...]
    rationale: str
    priority: InformationNeedPriority
    confidence: float
    provenance: dict[str, Any] = field(default_factory=dict)
    schema_version: str = INFORMATION_NEED_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)


@dataclass(frozen=True)
class RejectedInformationNeedSuggestion:
    suggestion: dict[str, Any]
    reason: str
    diagnostics: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)


@dataclass(frozen=True)
class EntityTypeDefinition:
    code: str
    display_name: str
    definition: str
    broader_group: str
    aliases: tuple[str, ...]
    example_signal_domains: tuple[str, ...]
    ontology_version: str
    schema_version: str = ENTITY_TYPE_DEFINITION_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)


@dataclass(frozen=True)
class EntityTypeCandidate:
    candidate_id: str
    entity_type_code: str
    display_name: str
    related_information_need_ids: tuple[str, ...]
    related_target_career_path_ids: tuple[str, ...]
    supported_monitoring_objectives: tuple[MonitoringObjectiveCode, ...]
    rationale: str
    discovery_terms: tuple[str, ...]
    confidence: float
    provenance: dict[str, Any] = field(default_factory=dict)
    schema_version: str = ENTITY_TYPE_CANDIDATE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)


@dataclass(frozen=True)
class ProposedEntityType:
    proposed_code: str
    display_name: str
    definition: str
    broader_group: str
    supporting_information_need_ids: tuple[str, ...]
    related_target_career_path_ids: tuple[str, ...]
    closest_canonical_type_codes: tuple[str, ...]
    why_canonical_types_are_insufficient: str
    rationale: str
    confidence: float
    provenance: dict[str, Any] = field(default_factory=dict)
    schema_version: str = PROPOSED_ENTITY_TYPE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)


@dataclass(frozen=True)
class RejectedEntityTypeSuggestion:
    suggestion: dict[str, Any]
    reason: str
    diagnostics: tuple[str, ...] = ()
    source_suggestion_index: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)


@dataclass(frozen=True)
class InformationNeedGenerationResult:
    monitoring_objectives: tuple[MonitoringObjectiveDefinition, ...]
    information_needs: tuple[InformationNeed, ...]
    rejected_suggestions: tuple[RejectedInformationNeedSuggestion, ...]
    diagnostics: tuple[str, ...]
    llm_execution_metadata: LLMExecutionMetadata
    input_fingerprint: str
    output_hash: str
    schema_version: str = INFORMATION_NEED_GENERATION_SCHEMA_VERSION
    generation_mode: str = "generated"

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)


@dataclass(frozen=True)
class EntityTypeExpansionResult:
    canonical_entity_types: tuple[EntityTypeDefinition, ...]
    canonical_candidates: tuple[EntityTypeCandidate, ...]
    proposed_new_types: tuple[ProposedEntityType, ...]
    rejected_suggestions: tuple[RejectedEntityTypeSuggestion, ...]
    uncovered_information_need_ids: tuple[str, ...]
    diagnostics: tuple[str, ...]
    llm_execution_metadata: LLMExecutionMetadata
    input_fingerprint: str
    output_hash: str
    schema_version: str = ENTITY_TYPE_EXPANSION_SCHEMA_VERSION
    generation_mode: str = "generated"

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)


def _json_ready(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value

    if is_dataclass(value) and not isinstance(value, type):
        return _json_ready(asdict(value))

    if isinstance(value, dict):
        return {
            str(key): _json_ready(item)
            for key, item in value.items()
        }

    if isinstance(value, tuple):
        return [
            _json_ready(item)
            for item in value
        ]

    if isinstance(value, list):
        return [
            _json_ready(item)
            for item in value
        ]

    return value
