# Source Monitoring Phase 6A Acquisition Planning

Phase 6A starts from the Phase 5 completion boundary. Phase 5 answered whether
a CandidateSource should be monitored. Phase 6 answers how an approved source
can be technically acquired. Phase 7 will execute a resolved acquisition
repeatedly and produce monitoring items.

Phase 6A is a planning and contract phase only. It does not fetch feeds, parse
RSS or Atom, call Brave, call DeepSeek, use browser automation, inspect new
webpages, execute Selected Website resolution, create production SourceItems,
or choose a final acquisition method.

## Responsibility Boundary

Phase 5 semantic approval remains independent from Phase 6 technical support.
A source can be `approved_for_acquisition` and later be
`unsupported` by current V1 acquisition methods without contradiction. Technical
difficulty must not downgrade semantic source value.

Phase 6A reads:

- Phase 5 canonical `source_evaluations.json`
- Phase 5F `phase6_source_handoff.json`
- existing CandidateSource records
- existing SourceInspection metadata and FeedLinkHints
- existing Phase 5E SourceObservationResult references

It writes:

- `outputs/planning/source_monitoring/acquisition_resolution_plans.json`

## Object Model

The Phase 6 chain is:

`Phase5AcquisitionHandoff -> AcquisitionResolutionPlan -> FeedVerificationPlan
-> FeedVerificationResult -> SelectedWebsiteResolutionPlan ->
SelectedWebsiteResolutionResult -> AcquisitionResolution ->
Phase7MonitoringHandoff`

Phase 6A defines these contracts and creates only planning objects:

- `AcquisitionResolutionPlan`: one approved source and the bounded resolution
  work to run next.
- `FeedVerificationPlan`: one existing feed candidate URL to verify in Phase 6B.
- `SelectedWebsiteResolutionPlan`: fallback planning for Phase 6C if no usable
  verified feed exists.
- Future result contracts for feed verification, Selected Website feasibility,
  final acquisition resolution, and Phase 7 handoff.

## Method vs Status

`AcquisitionMethod` is a small V1 product ontology:

- `rss`
- `atom`
- `selected_website`

`unsupported` and `needs_review` are not methods. They are
`AcquisitionResolutionStatus` values:

- `resolved`
- `needs_review`
- `unsupported`

`uncertain` evidence is not the same as `unsupported`. Phase 6D can mark a
source unsupported only when technical evidence is sufficient to show current
V1 methods cannot acquire it.

## Feed Hints vs Verified Feeds

Phase 5 `FeedLinkHint` records are unverified candidate evidence. Phase 6A
preserves href, normalized URL, rel, MIME type, title, source inspection ID,
source inspection hash, and hint index. It never writes `verified = true`.

Phase 6B owns actual feed verification. `FeedVerificationResult` separates:

- network outcome
- parse status
- verified feed format
- syntactic feed validity
- monitoring usability

A feed can parse successfully and still be unusable for monitoring if it lacks
usable item URLs or stable item identity support.

## Feed-First Strategy

V1 uses a deterministic strategy order:

1. verify existing known feed candidates;
2. if no usable verified feed exists, evaluate Selected Website acquisition;
3. if neither path resolves, Phase 6D returns `needs_review` or `unsupported`.

Feed-first is a technical acquisition preference. A verified RSS or Atom feed
is generally a more explicit machine-readable publication surface than HTML
item extraction. This does not imply greater semantic source value.

## Selected Website Fallback

Every approved source receives a Selected Website fallback plan unless a future
strong structural contract makes planning impossible. The fallback plan exists
even when feed candidates are present. Its execution dependency is explicit:

`execute_if_no_verified_usable_feed`

Phase 6C may reuse existing SourceInspection facts, representative link hints,
Phase 5E observation references, SourceRole, and technical limitation flags. It
must not need to rediscover everything from scratch.

## Identity and Fingerprints

Phase 6A identities are stable hashes over semantic inputs only:

- CandidateSource ID
- FinalSourceEvaluation fingerprint
- source URL
- observed SourceRole
- Phase 5 handoff fingerprint
- SourceInspection hash
- SourceObservationResult hash where present
- policy versions
- feed candidate URLs and hint evidence references

IDs do not include timestamps, local absolute paths, random UUIDs, diagnostics
paths, or mutable cache paths. Identical semantic inputs produce identical IDs,
fingerprints, ordering, and output hashes.

## Feed Candidate Deduplication and Budget

Known feed hints are normalized and deduplicated by candidate URL. If several
FeedLinkHints normalize to the same URL, Phase 6A preserves all evidence
references on the single FeedVerificationPlan.

The V1 maximum feed candidates to verify per source is `3`. Current approved
sources have at most two explicit feed hints, so the default covers current
data while preserving a small bounded fan-out. Overflow feed candidates are
stored as deferred records, not silently discarded.

## Future Phases

Phase 6B will verify feed candidates and produce `FeedVerificationResult`.

Phase 6C will evaluate Selected Website feasibility and produce
`SelectedWebsiteResolutionResult` and configuration when feasible.

Phase 6D will deterministically select final `AcquisitionResolution` from
Phase 6B/6C evidence. Only Phase 6D may set `acquisition_method`.

Phase 7 may receive only resolved acquisitions. `needs_review` and
`unsupported` resolutions cannot become Phase 7 monitoring handoffs.
