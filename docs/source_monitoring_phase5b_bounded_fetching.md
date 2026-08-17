# Source Monitoring Phase 5B Bounded Fetching

Phase 5B implements only the bounded network layer from the Phase 5A
contracts. It converts a `SourceFetchRequest` into `SourceFetchExecution`,
`RawPageArtifactRef`, and a transient `FetchedPage`.

It does not inspect HTML, parse feeds, validate PDFs, evaluate officiality,
match entities, assess SourceRole, assess InformationNeeds, run bounded
observation, call DeepSeek, call Brave, crawl pages, execute JavaScript, or
begin Phase 6.

## Safety Policy

One `SourceFetchRequest` means one explicit URL retrieval target plus configured
HTTP redirects. The fetcher does not discover or fetch links.

V1 uses a non-deceptive User-Agent, GET only, no authentication bypass, no
CAPTCHA or anti-bot circumvention, no login/session automation, bounded batch
size, bounded redirects, explicit timeout, and bounded response bytes.

HTTP failures such as 403, 404, 429, 500, and 503 are recorded as network facts.
They are not semantic source rejection.

## Defaults

Defaults are configured in `src/config.py`:

- `SOURCE_FETCH_TIMEOUT_SECONDS`: 15
- `SOURCE_FETCH_MAX_BYTES`: 2000000
- `SOURCE_FETCH_MAX_REDIRECTS`: 5
- `SOURCE_FETCH_USER_AGENT`: `AgentWorkflow/0.1 SourceFetcher (bounded source evaluation)`
- `SOURCE_FETCH_BATCH_SIZE`: 6

`SourceFetchPolicy` can override these values in tests or targeted smoke runs.

## Artifact Storage

Successful 2xx responses with accepted content types are written byte-for-byte
under:

`outputs/planning/source_monitoring/diagnostics/phase5_source_evaluation/raw_pages/`

The path is repository-relative in `RawPageArtifactRef`. Raw body identity is
the SHA-256 of the exact retrieved bytes. Phase 5B does not clean or transform
raw HTML.

Failure diagnostics are written under:

`outputs/planning/source_monitoring/diagnostics/phase5_source_evaluation/fetch_failures/`

Both locations are ignored generated planning artifacts.

## Cache Model

Compatible cache reuse treats persisted successful raw pages as immutable
historical fetch snapshots. A snapshot is reusable only when the full
`SourceFetchRequest` contract, request fingerprint, evaluation plan ID, and
CandidateSource ID match and the artifact hash still verifies.

Cache replay does not claim freshness and does not rewrite identical artifacts.

Failure diagnostics can also be replayed for the same request, plan, and
candidate IDs to avoid repeated external calls during deterministic validation.
They remain failure records and never produce a `FetchedPage`.

## Boundaries

HTML-like responses are recorded as completed HTML. PDF, XML/feed-looking,
plain text, JSON, and other recognized non-HTML types are recorded as completed
non-HTML. This is not content validation. RSS-like bytes do not mean a verified
feed, and PDFs do not mean a useful report source.
