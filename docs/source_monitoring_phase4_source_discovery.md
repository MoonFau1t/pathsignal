# Source Monitoring Phase 4: Candidate Source Discovery

Phase 3 establishes the prioritized entity set:

Entity Universe
        -> Entity Prioritization

Phase 4 uses those priorities to discover auditable candidate web sources:

Entity Priorities
        -> Discovery budgets
        -> Controlled Source Role selection
        -> Atomic candidate plans
        -> Deterministic plan ranking
        -> Budget truncation
        -> Executable plans
        -> Search evidence
        -> CandidateSources

All 62 Phase 3 entities remain auditable in Phase 4. Each entity receives one
or more executable plans, identity-resolution-only plans, or an explicit
deferral reason.

Tier controls the upper bound of executable plans. The maximum plan count is
not a quota, so an entity can use fewer plans when additional searches would
be low value. One plan is one atomic search operation with exactly one entity,
one Source Role, one language, one strategy, and one query. One executable
plan normally corresponds to one Brave Search request. English and Chinese
variants are separate plans and compete under the same budget.

The Source Role Ontology is universal and product-controlled. Personalization
can select and prioritize controlled roles, query language, discovery strategy,
query terms, and plan budget, but it cannot create or rename Source Roles.
Source Role is separate from Source Format Hint: a newsroom URL can be an HTML
page, RSS candidate, Atom candidate, or unknown format. RSS and Atom hints are
not feed verification.

Candidate plans are generated broadly, ranked deterministically, truncated by
budget, and preserved as executable or deferred records with explicit reasons.
Phase 4 discovers candidates but does not approve final monitoring sources,
create Source Registry records, fetch webpages, parse feeds, or create
SourceItems or CareerSignals.

Expected source usefulness is not observed source performance. Final quality,
freshness, cadence, feed validity, historical yield, and acquisition decisions
remain deferred to later phases.
