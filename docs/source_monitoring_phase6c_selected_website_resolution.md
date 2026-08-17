# Source Monitoring Phase 6C: Selected Website Resolution

Phase 6C resolves only the selected-website fallback plans that Phase 6B routed to `NO_USABLE_VERIFIED_FEED`.
It produces `SelectedWebsiteResolutionResult` records and, when feasible, embedded
`SelectedWebsiteAcquisitionConfig` records. It does not create `AcquisitionResolution`, select a final
`AcquisitionMethod`, verify feeds, discover new feeds, crawl item pages, follow pagination, run browser automation, call
Brave, or call an LLM.

## Inputs

- `outputs/planning/source_monitoring/acquisition_resolution_plans.json`
- `outputs/planning/source_monitoring/feed_verification_results.json`
- Phase 5C source inspections from `outputs/planning/source_monitoring/diagnostics/phase5_source_evaluation/inspections`
- Phase 5E observation references carried by the Phase 6A selected website plans

The execution population is derived from the Phase 6B `phase6c_routing` table:

- `NO_USABLE_VERIFIED_FEED` executes selected website resolution.
- `HAS_USABLE_VERIFIED_FEED` is excluded from Phase 6C selected website execution.

## Bounded Refresh

Each eligible selected website plan receives at most one source-surface refresh through `SourceFetcher`, using the
Phase 6C fetch policy:

- method: `GET`
- accepted content types: `text/html`, `application/xhtml+xml`
- max redirects: 5
- max response bytes: 1,000,000
- artifact root: `outputs/planning/source_monitoring/diagnostics/phase6_acquisition/selected_website_resolution/source_refreshes`

The fetched surface is inspected with `SourceInspector`, and current inspections are checkpointed under the Phase 6C
diagnostic namespace. Phase 6C never fetches discovered item URLs.

## Item Discovery

Phase 6C reuses Phase 5C semantic link clusters and Phase 5E helper functions:

- `extract_observation_item_candidates`
- `select_observation_items`

This preserves existing URL normalization, duplicate handling, pagination filtering, navigation filtering, role-aware
ranking, and date hints from URL paths. The resolver records candidate counts, title support, date-hint support,
normalized URL identity support, role compatibility, and in-scope link counts.

## Feasibility

A selected website is `feasible` when the current refreshed source surface exposes at least one in-scope normalized item
URL. Missing date hints are recorded as a limitation but do not block feasibility. If current evidence is missing while
historical Phase 5C evidence exists, the result is `needs_review`. A client-rendered surface with no item evidence is
`unsupported` because Phase 6C does not use browser automation.

Feasible results include a `SelectedWebsiteAcquisitionConfig` with:

- acquisition method: `selected_website`
- item discovery strategy: `selected_website_item_discovery_policy_v1`
- URL normalization policy: `normalize_source_url_v1_fragmentless`
- dedup identity policy: `selected_website_normalized_url_identity_v1`
- bounded `max_discovered_items_per_run`
- provenance references to Phase 6A, Phase 6B, current inspection, and historical inspection evidence

The config is preparatory evidence for Phase 6D. It is not a final acquisition resolution.

## Outputs

- `outputs/planning/source_monitoring/selected_website_resolution_results.json`
- `outputs/planning/source_monitoring/reports/phase6c_selected_website_resolution.md`
- `outputs/planning/source_monitoring/diagnostics/phase6_acquisition/selected_website_resolution/phase6c_selected_website_resolution_validation.json`

The result-set output hash excludes runtime-only cache-hit state. Replay validation compares the live result set with a
guarded cache replay and requires zero HTTP calls during replay.
