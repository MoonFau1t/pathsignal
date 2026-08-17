# Source Monitoring Phase 5C Deterministic Inspection

Phase 5C converts existing Phase 5B raw-page artifacts into deterministic
`SourceInspection` records. It is an offline interpretation layer only: it does
not fetch URLs, crawl links, execute JavaScript, call Brave, call DeepSeek, use
an LLM, observe feeds, validate PDFs, decide officiality, decide relevance, or
begin Phase 5D/6.

## Inputs

The inspector consumes completed Phase 5B `SourceFetchExecution` records with
`FetchStatus.COMPLETED_HTML`, an HTML content type, a `RawPageArtifactRef`, and
a matching raw body SHA-256. Non-HTML completions and failed/no-body executions
are accounted for as skipped outcomes with no fabricated source facts.

Raw artifact bytes remain the source of truth. The inspector verifies each body
against `RawPageArtifactRef.sha256` and `SourceFetchExecution.raw_body_sha256`
before parsing.

## Parser

HTML is parsed with BeautifulSoup using the `lxml` parser. The parser choice is
recorded in diagnostics and the inspection input fingerprint through
`SourceInspectionPolicy`.

Byte decoding is deterministic. A bounded raw prefix is checked for BOM and
HTML charset declarations before falling back to Phase 5B encoding metadata,
UTF-8, GB18030, and Latin-1. This preserves Unicode text when a fetch-layer
fallback such as ISO-8859-1 is less specific than the page itself.

## Extraction

The inspector records observable metadata only:

- title, meta description, Open Graph title/description, canonical URL, HTML
  language, and fetch-level content language
- bounded JSON-LD types and organization-like names from objects, lists, and
  `@graph` values
- h1/h2/h3 summaries and navigation labels
- normalized HTTP link counts split into internal, external, and same-domain
  counts
- deterministic link-surface hints for article, job, report, event, section,
  detail, and pagination patterns
- unverified RSS/Atom hints from HTML alternate-link tags
- bounded visible text and semantic windows for downstream evidence
- a low-text/high-script client-rendering-required hint

All link and feed URLs are resolved against HTML base/final URL context and
normalized with the Phase 4 source URL identity helpers.

## Text Window Policy

`SemanticTextWindow` payloads are bounded by policy:

- maximum 2,000 characters per window
- maximum 12,000 total semantic characters
- maximum 8 windows per inspection
- deterministic window ordering: title, meta description, headings,
  navigation, main/body excerpt, representative links, structured data

Script, style, noscript, template, and SVG content is excluded from visible text.
Main content is preferred when a `<main>` element exists; otherwise the body is
used with navigation, header, footer, and aside boilerplate removed.

Each window carries `UNTRUSTED_WEBPAGE_EVIDENCE_MARKER` provenance. Page text is
never treated as instructions.

## Identity

Inspection identity is deterministic and replayable. The input fingerprint
combines fetch execution identity, candidate identity, normalized final URL,
raw body SHA-256, inspector version, policy version, window limits, and parser.
The inspection ID is derived from the existing Phase 5A identity helper. The
output hash is computed from a canonical JSON-ready inspection payload with the
hash field blanked before hashing.

## Checkpoints

`persist_inspection_checkpoint` writes ignored, recomputable inspection
snapshots under:

`outputs/planning/source_monitoring/diagnostics/phase5_source_evaluation/inspections/`

Checkpoints are validation aids, not the durable contract. Re-running inspection
over the same raw artifacts produces the same inspection IDs, fingerprints,
output hashes, and semantic windows.

## Real-Artifact Validation

Offline validation used the existing Phase 5B artifacts under:

- `outputs/planning/source_monitoring/diagnostics/phase5_source_evaluation/raw_pages/`
- `outputs/planning/source_monitoring/diagnostics/phase5_source_evaluation/broader_fetch_validation/raw_pages/`

The validation report is ignored generated output at:

`outputs/planning/source_monitoring/reports/phase5c_deterministic_inspection_validation.md`

The diagnostic JSON is ignored generated output at:

`outputs/planning/source_monitoring/diagnostics/phase5_source_evaluation/phase5c_inspection_validation.json`

Validation accounting:

- 74 total Phase 5B execution records
- 20 completed HTML artifacts inspected
- 3 completed non-HTML artifacts skipped
- 51 failed/no-body executions skipped
- 20 unique completed-HTML raw SHA-256 values
- 0 duplicate completed-HTML raw SHA-256 records
- 0 HTTP, Brave, DeepSeek, browser, or LLM calls
- 0 determinism replay mismatches
- 0 raw artifact immutability changes

The HTML subset included 12 accepted candidates and 8 needs-review candidates.
Metadata coverage found 19 titles, 12 meta descriptions, 11 canonicals, 12 Open
Graph titles, 8 JSON-LD type sets, 15 heading summaries, 13 navigation label
sets, and 5 feed-link hint sets.

Compression from raw bytes to semantic windows was bounded: median semantic
window characters were 3.26% of raw bytes, with a maximum observed ratio of
20.28%.

## Phase 5D Handoff

Phase 5D can consume `SourceInspection` and semantic windows as untrusted,
bounded evidence. It should still treat feed hints as unverified, link hints as
surface observations, and client-rendering hints as weak fetch/inspection
signals rather than source-value decisions.
