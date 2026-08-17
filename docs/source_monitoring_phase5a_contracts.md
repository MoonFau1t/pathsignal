# Source Monitoring Phase 5A Contracts

Phase 5A defines contracts and data models only. It does not fetch live
webpages, parse real HTML, call Brave, call DeepSeek, validate feeds, create
database migrations, or begin Phase 6.

## 1. Phase 5 responsibility boundary

Phase 5 evaluates whether Phase 4 CandidateSources are suitable future
monitoring sources. The contract pipeline is:

CandidateSource -> SourceEvaluationPlan -> SourceFetchRequest ->
SourceFetchExecution -> RawPageArtifact / transient FetchedPage ->
SourceInspection -> SourceSemanticEvidenceBundle ->
InitialSourceEvaluation -> SourceObservationPlan ->
SourceObservationResult -> ObservedSignalPotential ->
FinalSourceEvaluation -> SourceEvaluationResult.

Each model keeps a narrow responsibility. Phase 5A stores the shape of future
evidence, inspection, semantic assessment, bounded observation, and final
evaluation without executing those steps.

## 2. Observation vs judgment

Observation is not judgment.

`SourceInspection`, `SourceFetchExecution`, `SourceObservationPlan`, and
`SourceObservationResult` record observed facts or bounded sampling outcomes.
They do not decide whether a source is good, official, relevant, durable, or
approved. Judgment lives in explicit assessment models, `InitialSourceEvaluation`,
`ObservedSignalPotential`, and `FinalSourceEvaluation`.

## 3. Python system truth vs DeepSeek semantic judgment

Network facts and deterministic page facts are Python-owned system truth.
Future DeepSeek use may assess semantic meaning only from compact structured
evidence. Assessment models record `assessment_method` as deterministic, llm,
or hybrid so Phase 5 can prefer deterministic Python logic where facts are
available.

## 4. Raw HTML trust boundary

All webpage content is untrusted external evidence. Webpage text must never be
treated as instructions to an LLM or to AgentWorkflow.

Raw HTML/body bytes are referenced through `RawPageArtifactRef` and content
hashes. They are not embedded in `SourceInspection`,
`SourceSemanticEvidenceBundle`, `InitialSourceEvaluation`, or
`FinalSourceEvaluation`. `FetchedPage` is explicitly a transient runtime
payload and its `to_dict()` omits raw bytes and decoded text.

## 5. Provenance chain

`SourceEvaluationPlan` preserves the Phase 4 candidate ID, entity ID, planned
SourceRole, Phase 4 status, supporting SourceDiscoveryEvidence IDs, allowed
InformationNeed IDs, input fingerprint, and Phase 4 cache hashes.

`SourceFetchRequest` fingerprints deterministic request policy. Future fetch
executions reference that fingerprint. `SourceFetchExecution` records what
happened at the network layer, including requested URL, final URL, redirect
hops, status taxonomy, HTTP metadata, body hash, and raw artifact reference.

`SourceInspection` references a fetch execution and raw body hash, then records
structured observed facts. `SourceSemanticEvidenceBundle` references an
inspection and carries only bounded evidence for semantic evaluation.

## 6. Phase 5A models

`SourceEvaluationPlan` is one auditable evaluation target for a Phase 4
CandidateSource.

`SourceFetchRequest` is deterministic future fetch intent. V1 is GET-only.

`SourceFetchExecution` records network-layer outcome and must not be interpreted
as semantic source acceptance or rejection.

`RedirectHop` preserves redirect provenance and hop order.

`RawPageArtifactRef` identifies persisted raw fetch material without embedding
raw content.

`FetchedPage` is a transient runtime fetch payload, not a persistent domain
model.

`SourceInspection` records URL, metadata, structural, format, extraction, and
provenance facts without semantic monitoring judgments.

`FeedLinkHint` records unverified feed-like alternate links only.

`SemanticTextWindow` is a bounded text unit permitted to cross the semantic
evaluation boundary.

`SourceSemanticEvidenceBundle` is the boundary between deterministic inspection
and future semantic judgment. It is compact, bounded, and explicitly marked as
untrusted webpage evidence.

`EntityMatchAssessment`, `OfficialityAssessment`, `PageTypeAssessment`,
`SurfaceDurabilityAssessment`, `SourceRoleAssessment`, and
`InformationNeedRelevanceAssessment` preserve field-level semantic judgments.

`InitialSourceEvaluation` decides only proceed_to_observation, needs_review, or
rejected.

`SourceObservationPlan` defines bounded sampling intent, not crawling.

`ObservedSourceEvidence` preserves bounded Phase 5 evidence without creating
production SourceItems.

`SourceObservationResult` summarizes one bounded observation execution.

`ObservedSignalPotential` categorizes signal potential from bounded evidence
without claiming long-term cadence.

`FinalSourceEvaluation` decides approved_for_acquisition, needs_review, or
rejected.

`SourceEvaluationResult` is the future top-level canonical Phase 5 contract.

## 7. Controlled enums

Phase 5A adds controlled enums for fetch status, evaluation scope, semantic
assessment statuses, page type, durability, role match, relevance, source value,
confidence, initial/final decisions, observation strategy/status, and observed
signal potential.

It reuses Phase 4 `SourceRole` and `SourceFormatHint`. It does not create a new
Phase 5 source role vocabulary.

## 8. Stable identity and fingerprint semantics

Identity helpers use SHA-256 over canonical JSON, matching upstream patterns.
Schema and policy versions are included where incompatible contract changes
should invalidate IDs or fingerprints.

Plan and request identities are stable intent identities. Fetch execution
identity is a snapshot identity: it includes plan/request linkage, final URL,
terminal fetch status, and raw body hash when available, but excludes
occurrence-only facts such as `retrieved_at` and elapsed time.

Inspection, semantic window, bundle, initial evaluation, observation plan,
observed evidence, final evaluation, and result hashes each include the stable
contract inputs that make the record reproducible.

## 9. InformationNeed relationship boundary

`SourceEvaluationPlan` and `SourceSemanticEvidenceBundle` carry allowed
InformationNeed IDs. `InformationNeedRelevanceAssessment` requires
supported_information_need_ids to be a subset of allowed_information_need_ids.

Future LLM evaluation must not create new entity-to-InformationNeed links from
the global dictionary inside Phase 5 evaluation. A separate proposed-link
mechanism would be required for that, and Phase 5A does not implement one.

## 10. SourceRole reuse from Phase 4

`SourceRoleAssessment` separates `planned_source_role` from
`observed_source_role`, but both use the Phase 4 product-level `SourceRole`
ontology. `SourceFormatHint` remains a separate format concept, so feed hints
cannot masquerade as roles.

## 11. One-off content vs durable source surface

Durable source surfaces include pages such as `/insights`, `/newsroom`,
`/research`, `/careers`, and `/portfolio`. One-off content includes article,
job, report, PDF, event, and profile detail pages.

Phase 5A preserves one-off content provenance through page type, durability,
initial evaluation rationale, review flags, and rejected evaluation records. A
page can be valuable information while still being unsuitable as a long-term
monitoring source.

## 12. Initial vs Final Evaluation

`InitialSourceEvaluation` occurs after inspection and semantic evidence
bundling but before bounded observation. It cannot approve acquisition.

`FinalSourceEvaluation` occurs after initial evaluation and, where needed,
bounded observation. It may use `approved_for_acquisition` to hand off to Phase
6, which will determine acquisition method.

## 13. Source Value vs Evaluation Confidence

Source value and evaluation confidence are separate categorical concepts. A
source can be high value with low confidence and therefore require review. Phase
5A does not invent numeric scoring weights.

## 14. Bounded observation meaning

`SourceObservationPlan` supports limited sampling with max item count and
optional lookback window. It models bounded evaluation sampling, not
unrestricted crawling or long-term monitoring.

`ObservedSourceEvidence` and `SourceObservationResult` remain Phase 5 evidence
and are not production SourceItem records.

## 15. ObservedSignalPotential limitations

`ObservedSignalPotential` uses high, medium, low, or insufficient_evidence. It
records sampled counts, relevant counts, InformationNeed hits, evidence IDs,
rationale, and limitations. It does not claim verified weekly/monthly cadence or
long-term source health.

## 16. Phase 5 vs Phase 6 boundary

Phase 5 decides whether a candidate source is approved for acquisition planning.
Phase 6 owns acquisition method selection, feed validation, crawler/fetch
strategy, database persistence, SourceItem production, and recurring monitoring.

## 17. Deferred implementation items for Phase 5B-5F

Phase 5B: implement fetch planning/execution against `SourceFetchRequest` and
`SourceFetchExecution`, including artifact persistence.

Phase 5C: implement deterministic page inspection and bounded extraction into
`SourceInspection`.

Phase 5D: implement semantic evidence bundle construction and future LLM
evaluation using bounded evidence only.

Phase 5E: implement bounded observation execution and observed source evidence
collection.

Phase 5F: implement final source evaluation production output and cache
generation. Phase 6 remains acquisition method resolution and production source
ingestion.
