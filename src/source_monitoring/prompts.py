import json
from typing import Any

from src.models import TargetCareerPath
from src.source_monitoring.entity_prioritization_models import (
    ENTITY_PRIORITIZATION_PROMPT_VERSION,
)
from src.source_monitoring.models import MonitoringObjectiveDefinition


INFORMATION_NEED_PROMPT_VERSION = "information_need_prompt_v1"
ENTITY_TYPE_EXPANSION_PROMPT_VERSION = "entity_type_expansion_prompt_v1"
ENTITY_DISCOVERY_PLANNING_PROMPT_VERSION = "entity_discovery_planning_prompt_v1"
ENTITY_CANDIDATE_EXTRACTION_PROMPT_VERSION = "entity_candidate_extraction_prompt_v1"


def build_information_need_prompt(
    *,
    target_career_paths: list[TargetCareerPath],
    user_preferences: dict[str, Any],
    monitoring_objectives: tuple[MonitoringObjectiveDefinition, ...],
    max_per_path_objective: int,
    max_total: int,
    max_signal_examples: int,
) -> str:
    """
    Build the strict JSON prompt for Source Monitoring Phase 0.
    """

    return f"""
Generate Source Monitoring Phase 0 InformationNeeds.

Task:
- Read all TargetCareerPaths together.
- Generate consolidated InformationNeeds across paths.
- Assign every InformationNeed to exactly one fixed MonitoringObjective.
- Associate every InformationNeed with one or more valid TargetCareerPath IDs.
- Merge cross-path needs when they describe the same monitoring requirement.
- Preserve distinctions when similar paths genuinely require different information.
- Respect UserPreferences, including preferred/excluded industries, geography,
  experience level, work environment, and AI plus business/industry/organization
  implementation preferences.
- Keep output broad enough for later entity discovery.

Strict boundary:
- Do not generate concrete company names.
- Do not generate websites, domains, URLs, RSS feeds, Search API queries,
  SearchPlans, entity types, articles, or concrete job openings.
- Do not claim any source or entity has been verified.
- Do not rename, remove, or add top-level MonitoringObjective categories.
- Do not include information_need_id, timestamps, fingerprints, hashes,
  provider metadata, or schema versions.

Limits:
- Maximum InformationNeeds per path per objective: {max_per_path_objective}
- Maximum total InformationNeeds: {max_total}
- Maximum signal_examples per InformationNeed: {max_signal_examples}

Return only valid JSON. Do not use markdown. The response must start with {{
and end with }}.

Return JSON with this exact top-level shape:
{{
  "information_needs": [
    {{
      "need_key": "stable_snake_case_key",
      "objective_code": "opportunity | organization | industry | career_path",
      "title": "Short human-readable name",
      "description": "Concrete monitoring need, without concrete entities or sources",
      "related_target_career_path_ids": ["existing_path_id"],
      "signal_examples": ["generic signal example"],
      "rationale": "Why this matters",
      "priority": "high | medium | low",
      "confidence": 0.0
    }}
  ]
}}

MonitoringObjectives:
{json.dumps([item.to_dict() for item in monitoring_objectives], ensure_ascii=False, indent=2)}

TargetCareerPaths:
{json.dumps([item.to_dict() for item in target_career_paths], ensure_ascii=False, indent=2)}

UserPreferences:
{json.dumps(user_preferences, ensure_ascii=False, indent=2)}
""".strip()


def build_entity_type_expansion_prompt(
    *,
    monitoring_objectives,
    information_needs,
    target_career_paths: list[TargetCareerPath],
    user_preferences: dict[str, Any],
    entity_type_ontology,
    max_canonical_candidates: int,
    max_proposed_types: int,
    max_types_per_need: int,
    max_discovery_terms: int,
) -> str:
    """
    Build the strict JSON prompt for Source Monitoring Phase 1.
    """

    return f"""
Generate Source Monitoring Phase 1 EntityTypeCandidates.

Task:
- Read all accepted Phase 0 InformationNeeds together.
- Use only accepted InformationNeeds as input. Rejected Phase 0 suggestions are
  audit evidence and are not Phase 1 input.
- Map every InformationNeed to one or more likely canonical Entity Types from
  the fixed ontology.
- Consolidate repeated entity types across several InformationNeeds.
- Prefer broad discovery recall while staying within the controlled ontology.
- Propose a new entity type only when no canonical type is adequate.
- Generate generic discovery_terms that can help Phase 2 discover concrete
  entities later.
- Canonical entity_type_code values are English controlled machine codes from
  the ontology. You may use listed aliases to understand meaning, but do not
  invent localized or Chinese canonical codes.
- Chinese InformationNeeds and UserPreferences are valid planning context.
  Rationales may remain in the configured planning language.
- discovery_terms are generic semantic phrases, not executable queries. They
  may include both English and Chinese terms for later bilingual discovery.
- For Chinese, China, or APAC-relevant candidates, include at least one English
  term and one Chinese term when semantically appropriate.

Strict boundary:
- Do not generate concrete company, fund, consultancy, regulator, media, or
  institution names.
- Do not generate websites, domains, URLs, RSS feeds, articles, jobs, Brave
  queries, SearchQueries, SearchPlans, SourceDiscoveryPlans, CandidateSources,
  provider metadata, IDs, fingerprints, hashes, schema versions, or timestamps.
- Do not modify, rename, remove, or add canonical ontology types.
- Do not use acquisition methods such as search_api, rss, or selected_website
  as entity types.
- Do not use Chinese phrases as candidate IDs, proposed_code values, or
  canonical entity_type_code values.
- Do not generate Brave syntax, Boolean search strings, domain filters, site:
  queries, URLs, or domains in discovery_terms. Phase 2 will independently
  generate Chinese and English SourceDiscoveryQueries from these terms.

Limits:
- Maximum canonical EntityTypeCandidates: {max_canonical_candidates}
- Maximum proposed new types: {max_proposed_types}
- Maximum entity types per InformationNeed: {max_types_per_need}
- Maximum discovery_terms per candidate or proposed type: {max_discovery_terms}

Return only valid JSON. Do not use markdown. The response must start with {{
and end with }}.

Return JSON with this exact top-level shape:
{{
  "entity_type_candidates": [
    {{
      "entity_type_code": "canonical_or_alias_type_code",
      "related_information_need_ids": ["existing_information_need_id"],
      "rationale": "Why this entity type can produce the required signals",
      "discovery_terms": ["generic semantic discovery concept"],
      "confidence": 0.0
    }}
  ],
  "proposed_new_types": [
    {{
      "proposed_code": "stable_snake_case_code",
      "display_name": "Human readable type name",
      "definition": "Concise definition",
      "broader_group": "closest broad group",
      "supporting_information_need_ids": ["existing_information_need_id"],
      "closest_canonical_type_codes": ["canonical_type_code"],
      "why_canonical_types_are_insufficient": "Specific ontology gap",
      "rationale": "Why this type may be needed later",
      "confidence": 0.0
    }}
  ]
}}

MonitoringObjectives:
{json.dumps([item.to_dict() for item in monitoring_objectives], ensure_ascii=False, indent=2)}

Accepted InformationNeeds:
{json.dumps([item.to_dict() for item in information_needs], ensure_ascii=False, indent=2)}

TargetCareerPaths:
{json.dumps([item.to_dict() for item in target_career_paths], ensure_ascii=False, indent=2)}

Canonical Entity Type Ontology:
{json.dumps([item.to_dict() for item in entity_type_ontology], ensure_ascii=False, indent=2)}

UserPreferences:
{json.dumps(user_preferences, ensure_ascii=False, indent=2)}
""".strip()


def build_entity_discovery_planning_prompt(
    *,
    entity_type_candidates,
    information_needs,
    target_career_paths: list[TargetCareerPath],
    user_preferences: dict[str, Any],
    entity_type_ontology,
    languages: tuple[str, ...],
    regions: tuple[str, ...],
    max_queries_per_type: int,
    max_total_plans: int,
) -> str:
    """
    Build the strict JSON prompt for Source Monitoring Phase 2 planning.
    """

    return f"""
Generate Source Monitoring Phase 2 EntityDiscoveryQuery proposals.

Task:
- Read all accepted Phase 1 EntityTypeCandidates together.
- Generate runtime discovery-query proposals for concrete real-world
  organizations, institutions, firms, platforms, or information producers.
- Use Phase 1 discovery_terms, multilingual ontology aliases, InformationNeeds,
  TargetCareerPaths, UserPreferences, languages, and regions as planning
  evidence.
- DeepSeek is responsible for semantic query content. Generate independent
  English and Chinese proposals when both languages are configured.
- Chinese proposals must use natural professional Chinese vocabulary and must
  not be mechanical translations of finished English query strings.
- Use canonical entity_type_code values exactly as English machine codes from
  the controlled ontology.
- Each query must target concrete entities. It may include official identity
  intent such as official website, organization, institution, fund, research
  center, association, platform, or firm.

Strict boundary:
- Do not generate query_id, plan_id, entity IDs, fingerprints, hashes, schema
  versions, timestamps, provider metadata, budgets, RSS feeds, approved sources,
  CandidateSources, SourceItems, CareerSignals, or OpportunitySearchPlans.
- Do not generate canonical entity_type_code values outside the ontology.
- Do not search primarily for current jobs, openings, applications, or hiring.
- For accelerators, venture studios, and ecosystem programs, search for the
  concrete organization, directory, official website, institution, platform, or
  fund identity. Do not make internships, fellowships, programs, openings, or
  applications the primary query intent.
- Do not return URLs, domains, RSS feeds, pages, job records, or approved
  monitoring sources as discovery plans.
- Do not use Brave-only search syntax such as site:, intitle:, inurl:, or
  filetype:.

Limits:
- Configured languages: {json.dumps(languages, ensure_ascii=False)}
- Configured regions: {json.dumps(regions, ensure_ascii=False)}
- Maximum query proposals per EntityTypeCandidate: {max_queries_per_type}
- Maximum total plan proposals: {max_total_plans}

Return only valid JSON. Do not use markdown. The response must start with {{
and end with }}.

Return JSON with this exact top-level shape:
{{
  "entity_discovery_queries": [
    {{
      "entity_type_code": "canonical_entity_type_code",
      "query_text": "runtime generated concrete-entity discovery query",
      "language": "configured_language",
      "region": "configured_region",
      "discovery_intent": "concrete_entity_discovery",
      "related_information_need_ids": ["existing_information_need_id"],
      "rationale": "Why this query is relevant to the supporting needs"
    }}
  ]
}}

Accepted Phase 1 EntityTypeCandidates:
{json.dumps([item.to_dict() for item in entity_type_candidates], ensure_ascii=False, indent=2)}

Accepted InformationNeeds:
{json.dumps([item.to_dict() for item in information_needs], ensure_ascii=False, indent=2)}

TargetCareerPaths:
{json.dumps([item.to_dict() for item in target_career_paths], ensure_ascii=False, indent=2)}

Canonical Entity Type Ontology:
{json.dumps([item.to_dict() for item in entity_type_ontology], ensure_ascii=False, indent=2)}

UserPreferences:
{json.dumps(user_preferences, ensure_ascii=False, indent=2)}
""".strip()


def build_entity_candidate_extraction_prompt(
    *,
    entity_discovery_evidence,
    entity_discovery_plans,
    max_entities_per_type: int,
) -> str:
    """
    Build the strict JSON prompt for bounded Phase 2 entity extraction.
    """

    return f"""
Extract concrete Source Monitoring Phase 2 EntityCandidate suggestions from
bounded Brave Search evidence.

Task:
- Search results are only evidence; do not treat every result as an entity.
- Extract concrete organizations, institutions, firms, platforms, or
  information producers only when supported by the supplied title, snippet, and
  URL evidence.
- Identify multilingual or alternate names, likely type labels, possible
  primary kind, possible facets, official-domain candidates, evidence IDs,
  confidence, and ambiguity notes.

Strict boundary:
- Do not approve monitoring sources, RSS feeds, website crawling configuration,
  SourceItems, CareerSignals, or current job opportunities.
- Do not decide final identity deduplication; Python will validate and
  consolidate.
- Reject articles, job postings, product names, generic categories, search
  queries, URLs without identifiable organizations, and unsupported entities.

Limit:
- Maximum entities per entity type: {max_entities_per_type}

Return only valid JSON. Do not use markdown. The response must start with {{
and end with }}.

Return JSON with this exact top-level shape:
{{
  "entity_candidates": [
    {{
      "canonical_name": "Organization name",
      "names_by_language": {{"en": ["Name"], "zh": ["Localized name"]}},
      "primary_entity_kind": "operating_company",
      "entity_type_codes": ["canonical_entity_type_code"],
      "classification_facets": {{"business_focus": ["technology"]}},
      "official_domain_candidates": [
        {{
          "domain": "example.com",
          "evidence_url": "https://example.com",
          "confidence": 0.0,
          "verification_status": "probable_official",
          "reason": "Why this domain may represent the entity"
        }}
      ],
      "supporting_evidence_ids": ["entity_evidence_id"],
      "geographic_scope": "global",
      "rationale": "Evidence-supported rationale",
      "confidence": 0.0,
      "ambiguity_notes": ""
    }}
  ]
}}

EntityDiscoveryPlans:
{json.dumps([item.to_dict() for item in entity_discovery_plans], ensure_ascii=False, indent=2)}

EntityDiscoveryEvidence:
{json.dumps([item.to_dict() for item in entity_discovery_evidence], ensure_ascii=False, indent=2)}
""".strip()


def build_entity_prioritization_prompt(
    *,
    compact_entity_contexts: tuple[dict[str, Any], ...],
    information_needs,
    target_career_paths: list[TargetCareerPath],
    user_preferences: dict[str, Any],
) -> str:
    """
    Build the strict JSON prompt for Source Monitoring Phase 3 prioritization.
    """

    return f"""
Generate Source Monitoring Phase 3 semantic entity-priority assessments.

Task:
- Assess every supplied compact entity context exactly once.
- Use the user's TargetCareerPaths, accepted InformationNeeds, UserPreferences,
  entity classifications, domain-candidate summaries, and representative
  evidence summaries.
- DeepSeek supplies semantic judgment only for path relevance, stage relevance,
  expected signal potential, and strategic importance.
- Use the controlled semantic scale: 0 no relevance or potential, 1 very weak,
  2 weak, 3 moderate, 4 strong, 5 very strong.
- For supporting_information_need_ids, use only IDs listed on that same compact
  entity context in related_information_need_ids. If no listed InformationNeed
  supports a dimension, return an empty list for that dimension rather than
  citing a globally valid but entity-unrelated need.

Critical boundary:
- expected_signal_potential is predictive. It asks whether the entity role and
  current Phase 2 evidence suggest this entity is likely to produce useful
  future signals.
- Do not claim observed_signal_potential, publication frequency, RSS
  availability, source reliability, historical signal yield, content freshness,
  or actual newsroom/careers quality.
- Do not use source-cadence phrases such as high publication frequency,
  regular publication, steady stream, continuous signal generation, daily
  updates, or frequent updates. Source cadence and source performance belong
  to later Source Discovery and Source Evaluation phases.
- Do not discover new entities, websites, pages, feeds, sources, jobs, or
  Source Discovery plans.

DeepSeek must generate:
- path_relevance_score and rationale;
- stage_relevance_score or null, stage_relevance_status, and rationale;
- expected_signal_potential_score and rationale;
- strategic_importance_score and rationale;
- supporting_information_need_ids;
- limiting_factors;
- review_flags;
- short_overall_rationale.

DeepSeek must not generate:
- priority assessment IDs, final aggregate scores, ranking, final tiers,
  scoring weights, geography scores, evidence-readiness scores, fingerprints,
  output hashes, timestamps, approved sources, RSS feeds, webpage
  recommendations, or Brave queries.

Stage relevance statuses:
- applicable;
- not_applicable;
- insufficient_evidence.

Stage relevance score rule:
- When stage_relevance.status is applicable, score must be an integer from 0 to 5.
- When stage_relevance.status is not_applicable or insufficient_evidence, score
  must be null, not 0.
- When stage_relevance.status is not_applicable, supporting_information_need_ids
  must be an empty list unless the cited ID appears in that entity context and
  directly explains stage applicability.

Return only valid JSON. Do not use markdown. The response must start with {{
and end with }}.

Return JSON with this exact top-level shape:
{{
  "entity_semantic_assessments": [
    {{
      "entity_id": "existing_entity_id",
      "path_relevance": {{
        "score": 0,
        "status": "assessed",
        "rationale": "bounded rationale",
        "supporting_information_need_ids": ["existing_information_need_id"],
        "limiting_factors": [],
        "review_flags": []
      }},
      "stage_relevance": {{
        "score": null,
        "status": "not_applicable",
        "rationale": "bounded rationale; use score null when status is not_applicable or insufficient_evidence",
        "supporting_information_need_ids": [],
        "limiting_factors": [],
        "review_flags": []
      }},
      "expected_signal_potential": {{
        "score": 0,
        "status": "assessed",
        "rationale": "bounded rationale about expected, not observed, signals",
        "supporting_information_need_ids": ["existing_information_need_id"],
        "limiting_factors": [],
        "review_flags": []
      }},
      "strategic_importance": {{
        "score": 0,
        "status": "assessed",
        "rationale": "bounded rationale",
        "supporting_information_need_ids": ["existing_information_need_id"],
        "limiting_factors": [],
        "review_flags": []
      }},
      "short_overall_rationale": "One concise overall rationale"
    }}
  ]
}}

Prompt version:
{ENTITY_PRIORITIZATION_PROMPT_VERSION}

Compact Entity Contexts:
{json.dumps(compact_entity_contexts, ensure_ascii=False, indent=2)}

Accepted InformationNeeds:
{json.dumps([item.to_dict() for item in information_needs], ensure_ascii=False, indent=2)}

TargetCareerPaths:
{json.dumps([item.to_dict() for item in target_career_paths], ensure_ascii=False, indent=2)}

UserPreferences:
{json.dumps(user_preferences, ensure_ascii=False, indent=2)}
""".strip()
