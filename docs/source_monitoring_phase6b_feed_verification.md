# Source Monitoring Phase 6B Feed Verification

Phase 6B consumes the Phase 6A `FeedVerificationPlan` population and produces
technical `FeedVerificationResult` evidence. It does not discover feeds, execute
Selected Website fallback, select an `AcquisitionMethod`, create an
`AcquisitionResolution`, or create a Phase 7 handoff.

## Phase 6A to 6B Contract

Phase 6A decides which feed candidates are eligible to verify. Phase 6B executes
only those planned URLs. A feed hint is evidence, not proof: the verifier must
fetch the exact candidate, preserve the raw response bytes, parse the bytes
locally, and classify the endpoint's technical validity and monitoring
usability.

## Discovery vs Verification

Candidate discovery and candidate verification are separate capabilities. Phase
6B does not guess `/feed`, `/rss`, `/rss.xml`, `/atom.xml`, or any other common
path. Hidden-feed discovery can be added only as an explicit future planning
capability.

## HTTP Success vs Feed Validity

HTTP success is only a network fact. A `200` response can still be malformed
XML, a normal HTML page, unrelated XML, or a structurally valid but insufficient
feed. The result preserves fetch status, parse status, recognized feed format,
syntax validity, and monitoring usability separately.

## XML Parser Boundary

The V1 parser uses Python's `xml.etree.ElementTree` against the already-fetched
raw bytes. The verifier refuses documents with a leading `DOCTYPE` marker and
does not resolve external entities, remote DTDs, external schemas, or network
resources referenced by XML. XML content is never executed, and regex is not used
as the primary parser.

## RSS Structure

RSS support recognizes an `rss` root with a `channel`. It extracts bounded
channel metadata (`title`, `link`, `description` presence by implication) and
document-order `item` entries. Entry extraction includes title, link, GUID,
publication-date text from `pubDate` or `date`, and summary presence.

## Atom Structure and Namespaces

Atom support recognizes a namespaced or unqualified `feed` root and
document-order `entry` elements. Feed and entry links support normal Atom `href`
attributes, with fallback to element text when present. Entry extraction includes
title, link, id, published/updated date text, and summary/content presence.

## Entry Identity and Dedup Capability

Phase 6B assesses whether future monitoring can deduplicate feed entries. RSS
GUID, Atom ID, or a normalized HTTP(S) entry URL can provide stable item identity.
Title alone is not a V1 identity source. Duplicate identities within the bounded
sample are reported as technical quality evidence, not automatic invalidity.

## Title and Date Capability

Title availability and publication-date evidence are measured independently.
Missing dates do not invalidate an otherwise usable feed if stable identity,
usable entry URLs, title support, and source relationship evidence are present.
Malformed date strings are counted separately from missing date evidence.

## Source and Feed Relationship

The verifier uses deterministic relationship evidence only: approved source
domain, feed final URL domain, normalized feed-level home link, and redirect
metadata. Same-domain and feed-home-link-related feeds can be usable. Unresolved
cross-domain feeds require review rather than silent acceptance.

## Bounded Sampling

The default V1 sample limit is 20 entries per feed. The verifier preserves total
entry count where cheaply available, sampled count, and document order. It does
not infer recency from document order and does not fetch pagination, archives, or
entry URLs.

## Failure Taxonomy

Fetch failures, unsupported content, empty bodies, parse failures, invalid
non-feed XML, empty valid feeds, valid-but-limited feeds, and usable feeds are
represented separately through `FeedVerificationStatus`, `FeedParseStatus`, and
reason codes.

## Cache and Replay

Phase 6B reuses `SourceFetcher` with a Phase 6B artifact namespace. Successful
raw feed bytes are immutable snapshots keyed by fetch request and body SHA.
Compatible replay uses the cached fetch outcome and re-parses the same bytes
without HTTP. Cached controlled failures also replay without another request.

## Phase 6C Boundary

Phase 6B derives routing only: `HAS_USABLE_VERIFIED_FEED` or
`NO_USABLE_VERIFIED_FEED`. Sources with no known feed candidates, such as the
current Qianzhan source, route to `NO_USABLE_VERIFIED_FEED` with reason
`no_known_feed_candidate_verified`. Phase 6C owns Selected Website execution.

## Phase 6D Boundary

Phase 6D owns final acquisition resolution. A usable feed result is evidence for
Phase 6D; it is not itself a final selected acquisition method.
