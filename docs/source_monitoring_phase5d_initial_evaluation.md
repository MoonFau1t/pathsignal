# Source Monitoring Phase 5D Initial Evaluation

Phase 5D converts existing Phase 5C `SourceInspection` records into
`InitialSourceEvaluation` records. It is an initial semantic judgment layer only.
It does not fetch pages, call Brave, crawl links, execute JavaScript, observe
source items, compute `ObservedSignalPotential`, create observation plans, or
approve acquisition.

## Inputs

The evaluator consumes only existing planning artifacts:

- Phase 5C `SourceInspection` checkpoints
- Phase 4 `CandidateSource` context
- Phase 2 `EntityCandidate` identity and official-domain evidence
- Phase 0 `InformationNeed` records already related to the entity

Raw HTML is not accepted by the evaluator. Webpage-derived evidence reaches the
LLM only through bounded `SemanticTextWindow` values and structured inspection
metadata.

## Semantic Bundle

`SourceSemanticEvidenceBuilder` builds the Phase 5A
`SourceSemanticEvidenceBundle` deterministically. The bundle contains entity
identity context, candidate source context, inspection metadata, unverified feed
hints, structural hints, bounded semantic windows, allowed source roles, and
allowed InformationNeed IDs.

The prompt payload is derived from that bundle and adds concise InformationNeed
text for only the allowed IDs. Every prompt includes the untrusted webpage
content boundary and an explicit list of allowed evidence references.

## Size Policy

The versioned bundle policy bounds:

- total semantic text characters
- entity aliases
- known official-domain evidence
- headings and navigation labels
- structural hints
- allowed InformationNeeds
- candidate facts and evidence references

IDs needed for validation are not truncated. Bundle identity uses canonical
fingerprints so compatible cache replay is deterministic.

## Deterministic And LLM Responsibilities

Python owns system truth and validation:

- exact domain comparisons
- candidate/entity IDs
- allowed InformationNeed ID subsets
- controlled enum values
- source role ontology membership
- output/cache fingerprints
- initial decision policy

DeepSeek is limited to semantic interpretation of supplied evidence:

- ambiguous entity belonging
- official vs affiliated vs third-party interpretation
- page type
- source-surface durability
- observed source role
- InformationNeed relevance
- concise rationale

Deterministic assessments are preserved where evidence is conclusive. Compatible
LLM assessments are merged as hybrid. Conflicting LLM assessments are retained as
review flags without overwriting deterministic system evidence.

## Output

The evaluator produces `InitialSourceEvaluation` only, with deterministic
decisions from the existing enum:

- `proceed_to_observation`
- `needs_review`
- `rejected`

Rejected one-off content keeps provenance. Low confidence does not automatically
mean low source value. Proceeding to observation is not acquisition approval and
does not create a Phase 5E observation plan.

## Validation Runner

`scripts/run_phase5d_initial_evaluation_validation.py` performs the requested
live validation when external DeepSeek calls are explicitly allowed. It loads the
existing corpus, selects a deterministic smoke sample, runs entity-scoped
bounded batches, evaluates all compatible Phase 5C inspections after smoke
passes, verifies cache replay with `GuardInitialEvaluationClient`, checks
checkpoint/upstream immutability, and writes ignored generated outputs:

- `outputs/planning/source_monitoring/diagnostics/phase5_source_evaluation/initial_evaluations.json`
- `outputs/planning/source_monitoring/diagnostics/phase5_source_evaluation/phase5d_initial_evaluation_validation.json`
- `outputs/planning/source_monitoring/reports/phase5d_initial_source_evaluation.md`
