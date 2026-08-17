# Source Monitoring Phase 5F Final Source Evaluation

Phase 5F is the offline evidence-composition layer for the Source Monitoring
Phase 5 pipeline. It consumes the already persisted Phase 5D
`InitialSourceEvaluation` records and Phase 5E bounded observation records, then
produces one `FinalSourceEvaluation` per current Phase 5D candidate.

Phase 5F performs no HTTP, Brave, DeepSeek, browser, crawling, URL discovery, or
new inspection work. It does not choose an acquisition method. Phase 6 is
responsible for acquisition-resolution planning after Phase 5 closeout.

## Inputs

- `InitialSourceEvaluation`
- optional Phase 5E observation eligibility record
- optional `SourceObservationPlan`
- optional `ObservedSourceEvidence`
- optional `SourceObservationResult`
- optional `ObservedSignalPotential`

Every Phase 5D candidate must reconcile to exactly one final evaluation. Missing
Phase 5E observation evidence is represented as insufficient observation
evidence, not as negative evidence.

## Evidence Composition

The policy is categorical and deterministic. It does not use a numeric master
score and no single soft dimension decides the final state.

The composition trace keeps five dimensions explicit:

- identity foundation: entity match and officiality
- source-surface suitability: page type, durability, and SourceRole fit
- information fit: Phase 5D relevance and observed allowed InformationNeed hits
- observation evidence: bounded sample size, failures, item evidence, and
  ObservedSignalPotential
- evidence quality: Phase 5D confidence, observation completeness, limitations,
  and conflicts

The persisted `FinalEvidenceCompositionTrace` stores IDs, categorical states,
positive evidence, counter-evidence, unresolved uncertainty, conflicts, reason
codes, policy version, and a stable fingerprint. It references upstream evidence
by ID/hash rather than copying raw webpage artifacts.

## Hard Blockers

Phase 5F recognizes only a narrow set of final hard blockers:

- `entity_mismatch`
- `third_party_source_for_entity_surface`
- `one_off_detail_page`

The following are not hard blockers by themselves:

- missing RSS/feed hints
- missing canonical metadata
- missing JSON-LD
- low EvaluationConfidence
- low ObservedSignalPotential
- absent Phase 5E observation
- client-rendering hints
- fetch difficulty
- language or Unicode content

## Decisions

Final decisions are:

- `approved_for_acquisition`
- `needs_review`
- `rejected`

Approval requires sufficient identity and surface foundations, meaningful
InformationNeed fit, supportive bounded observation where required, adequate
quality, and no unresolved material conflicts. A medium observed signal can
support approval, but medium alone is not sufficient.

Rejection is limited to persistent hard blockers or convergent negative evidence:
for example, Phase 5D low InformationNeed relevance plus a complete low Phase 5E
sample with no relevant allowed-need coverage. Low observed signal alone is not
enough.

Needs-review preserves unresolved identity, officiality, surface, information,
observation, or quality uncertainty. It is also used when older Phase 5D
rejections lack a persistent Phase 5F hard blocker.

## Outputs

- `outputs/planning/source_monitoring/diagnostics/phase5_source_evaluation/final_source_evaluations.json`
- `outputs/planning/source_monitoring/source_evaluations.json`
- `outputs/planning/source_monitoring/diagnostics/phase5_source_evaluation/phase6_source_handoff.json`
- `outputs/planning/source_monitoring/diagnostics/phase5_source_evaluation/phase5f_final_evaluation_validation.json`
- `outputs/planning/source_monitoring/reports/phase5f_final_source_evaluation.md`

The Phase 6 handoff contains only approved sources, supported
InformationNeed IDs, source role context, source value, confidence, and reason
codes. It explicitly does not choose an acquisition method.
