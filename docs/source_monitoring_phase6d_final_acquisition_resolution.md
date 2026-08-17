# Source Monitoring Phase 6D: Final Acquisition Resolution

Phase 6D is an offline deterministic evidence-composition phase. It reads completed Phase 6A, 6B, and 6C artifacts and
creates the canonical Phase 6 acquisition result:

- `AcquisitionResolution` records for every approved source
- `Phase7MonitoringHandoff` records for every resolved source
- needs-review and unsupported technical acquisition provenance

It does not perform HTTP, feed parsing, HTML inspection, browser automation, Brave search, DeepSeek calls, LLM calls,
SourceItem creation, monitoring execution, or Phase 7 execution.

## Responsibility

Phase 5 answers whether a source should be monitored. Phase 6 answers how an approved source can technically be
acquired. Phase 7 executes the resolved acquisition repeatedly and produces monitoring items.

Phase 6D does not revisit semantic source value. It resolves only completed technical evidence.

## Resolution Policy

The V1 policy is feed-first:

1. Prefer verified usable RSS/Atom feed evidence from Phase 6B.
2. Otherwise use feasible selected-website evidence from Phase 6C.
3. Otherwise emit `needs_review` for incomplete/conflicting evidence.
4. Otherwise emit `unsupported` when completed evidence establishes no supported V1 acquisition method.

`resolution_status` and `acquisition_method` remain separate. `resolved` requires a method. `needs_review` and
`unsupported` require a null method.

## Feed Resolution

Feed acquisition requires a completed `FeedVerificationResult`; a `FeedLinkHint` or `FeedVerificationPlan` alone is not
enough. A usable feed must have:

- `verification_status = verified_usable`
- recognized format `rss` or `atom`
- syntax validity
- monitoring usability
- usable entry URLs

When a feed wins, Phase 6D selects `acquisition_method = rss` or `atom` according to the verified format.

Verification does not replace runtime parsing. Phase 7 should still fetch and parse the RSS/Atom payload during normal
monitoring runs, but it should not have to repeat Phase 6B verification to know the endpoint is an RSS/Atom acquisition
route.

## Multiple Usable Feeds

If more than one usable feed exists for a source, Phase 6D uses deterministic primary-feed selection:

1. strongest source relationship evidence
2. stable identity support
3. usable entry URL count
4. title/date capability
5. fewer limitations
6. normalized feed URL tie-break

The current `AcquisitionResolution` contract represents one active V1 feed endpoint. Alternate usable feeds are not
discarded; they are preserved in Phase 6D multi-feed audit records and Phase 7 handoff provenance.

## Selected Website Resolution

Selected website acquisition resolves only when Phase 6C produced:

- `SelectedWebsiteResolutionResult.feasibility_status = feasible`
- an embedded `SelectedWebsiteAcquisitionConfig`

Phase 6D preserves the exact config reference. It does not regenerate the config and does not rerun selected-website
discovery.

## Needs Review And Unsupported

`needs_review` is used for incomplete or conflicting technical evidence, such as missing Phase 6C fallback evidence,
transient feed failures, selected website `needs_review`, or a feasible selected website result missing its config.

`unsupported` is used when completed Phase 6 evidence establishes that V1 supports neither usable RSS/Atom nor feasible
selected website acquisition. This does not modify the Phase 5 approved source state.

## Phase 7 Handoff

Phase 7 handoffs are compact and execution-oriented. Feed handoffs preserve the selected verified feed URL, format,
verification result ID, parser policy reference, item identity strategy, bounded Phase 6B capability evidence, and
alternate feed provenance. Selected website handoffs preserve the Phase 6C acquisition config ID and compact strategy
references.

Handoffs do not include raw XML, raw HTML, prompts, Phase 5 semantic evidence bundles, large link samples, or monitoring
cadence. Phase 7 owns runtime execution and cadence lifecycle.

## Outputs

Canonical Phase 6 output:

- `outputs/planning/source_monitoring/acquisition_resolutions.json`

Validation and report outputs:

- `outputs/planning/source_monitoring/diagnostics/phase6_acquisition/final_acquisition_resolution/phase6d_final_acquisition_resolution_validation.json`
- `outputs/planning/source_monitoring/reports/phase6d_final_acquisition_resolution.md`

The semantic output hash excludes timestamps, local paths, cache metadata, and random values. Replaying unchanged
Phase 6A/B/C artifacts must produce identical resolution IDs, decisions, handoff IDs, reason codes, fingerprints, and
output hash with zero external calls.

## Future Revalidation

Future phases may define revalidation policies for when feed or selected-website evidence should be refreshed. That is
outside Phase 6D. Phase 6D closes the current Phase 6 evidence set only.
