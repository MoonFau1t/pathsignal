# AgentWorkflow V1
# Career Intelligence Interpretation Contract V1

Status: V1 Design Contract

Contract Version:

`career_intelligence_interpretation_v1`

Purpose:

Define how already-filtered, already-scored Intelligence CareerSignals are
synthesized into evidence-grounded Themes, Key Developments, and Career
Implications for the user's current career directions.

This contract belongs to AgentWorkflow V1 Closing Stage 4.

It is the authoritative source of truth for future prompt rendering, strict
response parsing, and runtime orchestration in Closing Stage 4B.

It does NOT implement or authorize:

- an LLM client;
- a response parser;
- runtime or pipeline integration;
- database persistence;
- Closing Stage 5 Final Brief generation.

============================================================
1. PURPOSE
============================================================

Closing Stage 4 is a multi-signal Career Intelligence Interpretation layer.

Its primary input is the Closing Stage 3 `intelligence` bucket. It interprets
multiple supplied Intelligence CareerSignals together to answer three distinct
questions:

1. Theme:
   "What broader user-relevant patterns are emerging across the supplied
   Intelligence signals?"

2. Key Development:
   "What concrete recent developments, focal changes, or potential
   technological breakthroughs deserve attention?"

3. Career Implication:
   "What do those developments mean for the user's current career paths?"

Closing Stage 4 is Intelligence-first. It does not primarily interpret
individual job Opportunities.

============================================================
2. SCOPE AND RESPONSIBILITY
============================================================

Closing Stage 4 consumes already-normalized, already-filtered, already-scored,
and already-routed Intelligence signals. It may synthesize and draw bounded
inferences from the supplied evidence.

Its output consists only of:

- Themes;
- Key Developments;
- Career Implications;
- warnings about evidence limitations.

The three interpretation object types have separate responsibilities:

| Object | Question | Minimum grounding |
|---|---|---|
| Theme | What broader user-relevant pattern is emerging across multiple signals? | At least two distinct supplied Intelligence signals |
| Key Development | What concrete recent event or change deserves attention? | At least one supplied Intelligence signal |
| Career Implication | What might supplied external developments mean for current career paths? | At least one supplied Intelligence signal and at least one supplied TargetCareerPath |

Closing Stage 4 does not perform its main interpretation workflow over Closing
Stage 3 `opportunities`. Opportunities retain their CareerSignal,
PriorityAssessmentResult, and PriorityScoreResult for Closing Stage 5.

Closing Stage 4 may discuss career directions affected by Intelligence
developments. It must not reinterpret, rescore, recommend, or assess application
feasibility for a specific job Opportunity.

============================================================
3. INPUTS
============================================================

------------------------------------------------------------
3.1 Primary Runtime Input
------------------------------------------------------------

The primary runtime input is:

`CareerSignalRoutingResult.intelligence`

Every item in that bucket preserves:

- CareerSignal;
- PriorityAssessmentResult;
- PriorityScoreResult.

The future implementation should send only information needed for grounded
interpretation. Selected information may include the fields below.

CareerSignal:

- `signal_id`;
- `category`;
- `title`;
- `organization`;
- `published_at`;
- `summary`;
- relevant source evidence where available;
- `matched_career_path_ids`.

PriorityAssessmentResult:

- Intelligence semantic assessment components;
- concise component reasons and evidence where relevant.

PriorityScoreResult:

- `priority_score`;
- `tier`;
- relevant component information where useful.

All signals supplied to one interpretation call form the complete factual
evidence boundary for that call.

------------------------------------------------------------
3.2 TargetCareerPaths
------------------------------------------------------------

The request also supplies the current TargetCareerPaths needed to establish
user relevance. The future implementation should provide bounded path context,
including stable path IDs and only the current path details useful for
interpretation.

Every returned `relevant_career_path_id` must belong to the exact set of current
TargetCareerPaths supplied in the request.

The interpretation layer must not:

- create a new CareerPath;
- rename or replace a supplied path ID;
- infer or rematch a CareerPath outside the supplied set;
- redo upstream CareerPath matching.

------------------------------------------------------------
3.3 UserPreferences
------------------------------------------------------------

Bounded UserPreferences relevant to career interpretation may be supplied to
keep the result aligned with the user's career interests and constraints.

UserPreferences establish why an external development may matter to the user.
They are not factual evidence that an external event, market shift, company
action, or technological development occurred.

Missing UserPreferences must not cause the model to invent preferences or
external facts.

------------------------------------------------------------
3.4 UserProfile
------------------------------------------------------------

The full UserProfile is omitted from Closing Stage 4 semantic input by default.

Closing Stage 4 does not evaluate:

- candidate qualification;
- application feasibility;
- resume fit;
- offer probability.

Those responsibilities belong upstream. A future contract version may revisit
the UserProfile boundary if a concrete need is established, but V1 does not
provide the full UserProfile by default.

------------------------------------------------------------
3.5 Priority Score
------------------------------------------------------------

PriorityScoreResult is authoritative upstream runtime context. It may help the
interpretation layer identify which already-scored Intelligence signals deserve
greater attention.

Closing Stage 4 must not:

- recalculate Priority Score;
- change Priority Score;
- create a replacement score;
- override deterministic ranking policy.

Interpretation confidence is not another Priority Score.

============================================================
4. INTERPRETATION OBJECTS
============================================================

------------------------------------------------------------
4.1 Theme
------------------------------------------------------------

Definition:

A Theme is a broader, user-relevant pattern, trend, or directional development
supported by multiple Intelligence CareerSignals and materially relevant to one
or more current TargetCareerPaths.

A Theme answers:

"What do multiple supplied signals collectively suggest is happening?"

A Theme is not:

- a generic topic label;
- a single news event;
- a rewrite of one CareerSignal;
- a CareerPath name;
- a user preference;
- an unsupported industry prediction.

Invalid Themes include:

- "AI";
- "Consulting";
- "Venture Capital";
- "Enterprise Software".

A valid Theme expresses a directional interpretation, such as:

"Enterprise AI activity is shifting from experimentation toward operational
implementation and organizational adoption."

Every Theme must contain exactly these fields:

- `title`: a non-empty string;
- `summary`: a non-empty string;
- `supporting_signal_ids`: an array containing at least two distinct, non-empty
  signal IDs from `input_signal_ids`;
- `relevant_career_path_ids`: an array containing zero or more distinct,
  non-empty path IDs from the supplied current TargetCareerPaths;
- `confidence`: exactly `high`, `medium`, or `low`.

`relevant_career_path_ids` is an optional reference annotation for a Theme, not
the Theme's semantic relevance gate. An empty array is schema-valid, but it does
not waive the requirement that the Theme be materially relevant to at least one
current TargetCareerPath. It must not be used to admit a generic or
user-irrelevant Theme.

A Theme must not be emitted merely because several signals share a keyword.
The directional relationship must be substantively supported by the supplied
evidence.

One isolated signal can never support a V1 Theme. If no defensible multi-signal
Theme exists, `themes` must be an empty array.

------------------------------------------------------------
4.2 Key Development
------------------------------------------------------------

Definition:

A Key Development is a concrete recent event, development, organizational
action, market shift, industry focus, or evidence-backed potential
technological breakthrough that deserves attention within the user's career
intelligence context.

A Key Development answers:

"What specifically happened or appears to be developing that is worth
attention?"

Examples may include:

- a major organization launching a new AI transformation practice;
- expansion of AI into a concrete enterprise workflow;
- a meaningful funding or investment development;
- a material company strategic shift;
- an emerging technical capability supported by supplied evidence.

Key Development describes what happened. Theme describes what multiple
developments collectively suggest.

Every Key Development must contain exactly these fields:

- `title`: a non-empty string;
- `summary`: a non-empty string;
- `why_it_matters`: a non-empty string;
- `supporting_signal_ids`: an array containing at least one distinct, non-empty
  signal ID from `input_signal_ids`;
- `confidence`: exactly `high`, `medium`, or `low`.

One signal may support a Key Development when the event itself is specific and
substantively important. Multiple signals may support the same development when
they provide distinct evidence of that same event or development.

Generic statements such as "AI remains important" do not qualify as Key
Developments.

------------------------------------------------------------
4.3 Potential Technological Breakthroughs
------------------------------------------------------------

The interpretation layer must not overclaim technological breakthroughs.

When supplied evidence is suggestive but not conclusive, a development may be
described only with uncertainty-preserving language such as:

- potential;
- emerging;
- early indication;
- preliminary.

The model must not upgrade a product announcement, marketing claim, single
company statement, or weak signal into a confirmed technological breakthrough
without supporting evidence contained in the supplied CareerSignals.

Closing Stage 4 must preserve uncertainty and distinguish direct description
from inference.

------------------------------------------------------------
4.4 Career Implication
------------------------------------------------------------

Definition:

A Career Implication is an evidence-backed interpretation of how one or more
supplied external developments may affect the user's current TargetCareerPaths,
role demand, skill requirements, career opportunity structure, or career-path
risks.

A Career Implication answers:

"What might these developments mean for the user's current career directions?"

Valid subject matter may include:

- changing role demand;
- changing skill expectations;
- changing role responsibilities;
- emerging opportunity areas;
- risks to a career path;
- relative importance of capabilities;
- changes in the relationship between business, technology, investment,
  strategy, or organization;
- possible changes to the opportunity landscape.

Every Career Implication must contain exactly these fields:

- `summary`: a non-empty string;
- `relevant_career_path_ids`: an array containing at least one distinct,
  non-empty path ID from the supplied current TargetCareerPaths;
- `supporting_signal_ids`: an array containing at least one distinct, non-empty
  signal ID from `input_signal_ids`;
- `confidence`: exactly `high`, `medium`, or `low`.

Every Career Implication must express this relationship:

```
supplied external development
->
potential effect on a supplied current career path
```

It must not merely restate UserPreferences or TargetCareerPath descriptions.

Invalid:

"The user is interested in AI strategy."

Valid:

"Growing emphasis on enterprise AI implementation may increase the value of
implementation and organizational-transformation experience within AI Strategy
roles."

============================================================
5. CONFIDENCE SEMANTICS
============================================================

Closing Stage 4 introduces no new numeric score.

Interpretation confidence is categorical and must be exactly one of:

- `high`;
- `medium`;
- `low`.

Definitions:

### high

The interpretation is directly supported by multiple specific and mutually
consistent supplied signals.

### medium

The interpretation has meaningful support, but evidence is limited,
concentrated in a small number of signals, or still requires confirmation.

### low

The interpretation is an early indication, indirect inference, or is supported
by relatively weak or limited current evidence.

Confidence means:

"How strongly is this interpretation supported by the supplied evidence?"

It does not mean:

- probability of receiving a job offer;
- probability a company succeeds;
- probability an industry forecast becomes true;
- user success probability;
- another Priority Score.

============================================================
6. EVIDENCE GROUNDING
============================================================

------------------------------------------------------------
6.1 Input Signal Identity
------------------------------------------------------------

`input_signal_ids` identifies the actual CareerSignals supplied to the
interpretation call.

The returned `input_signal_ids` must have exact set equality with the supplied
interpretation signal IDs:

- no supplied ID may be missing;
- no additional ID may be returned;
- no ID may be invented;
- IDs must be non-empty strings and unique within the array.

Array order carries no semantic meaning. A future implementation may choose a
canonical deterministic order, but set equality is the V1 validation rule.

------------------------------------------------------------
6.2 Supporting Signal Grounding
------------------------------------------------------------

Every `supporting_signal_id` in every interpretation object must exist in
`input_signal_ids`.

No interpretation object may cite an unknown or external signal. The model may
not invent factual support that is not represented by the supplied signals.

Closing Stage 4 is synthesis, not a new search or research step.

------------------------------------------------------------
6.3 Target Career Path Grounding
------------------------------------------------------------

Every `relevant_career_path_id` must exist in the exact set of current
TargetCareerPaths supplied to the interpretation request.

The model may not create, rename, replace, infer, or rematch CareerPaths outside
that supplied set.

------------------------------------------------------------
6.4 Evidence and Relevance Roles
------------------------------------------------------------

The evidence roles are separate:

```
CareerSignal evidence
->
supports factual claims about the external world

UserPreferences and TargetCareerPaths
->
establish why supplied external developments matter to the user
```

UserPreferences and TargetCareerPaths cannot serve as factual evidence that an
external event or trend occurred.

------------------------------------------------------------
6.5 No External Knowledge
------------------------------------------------------------

The Closing Stage 4 LLM must base factual interpretation on information
supplied in the request.

It must not supplement claims with unsupported outside facts, remembered market
knowledge, or assumptions about companies or industries that are not contained
in supplied evidence.

The model may synthesize and infer from supplied signals, but it must preserve
uncertainty when an inference extends beyond direct factual description.

============================================================
7. OUTPUT QUANTITY LIMITS
============================================================

V1 output limits are:

| Output | Minimum | Maximum |
|---|---:|---:|
| `themes` | 0 | 5 |
| `key_developments` | 0 | 8 |
| `career_implications` | 0 | 5 |

The model is not required to fill these limits. Quality and evidence grounding
take priority over producing the maximum number of items.

Empty arrays are valid.

============================================================
8. CANONICAL JSON RESPONSE CONTRACT
============================================================

The response must be a JSON object with exactly these top-level keys:

- `schema_version`;
- `input_signal_ids`;
- `themes`;
- `key_developments`;
- `career_implications`;
- `warnings`.

No extra top-level keys are allowed. In particular, the response must not
contain:

- `priority_score`;
- `overall_score`;
- `recommendation`;
- `action_plan`;
- `final_summary`;
- `market_score`;
- `trend_score`.

The following is a valid concrete instance of the contract, assuming
`signal_001` and `signal_002` are the complete supplied interpretation signal
set and `path_001` is a supplied current TargetCareerPath:

```json
{
  "schema_version": "career_intelligence_interpretation_v1",
  "input_signal_ids": [
    "signal_001",
    "signal_002"
  ],
  "themes": [
    {
      "title": "Enterprise AI moves toward operational implementation",
      "summary": "Multiple supplied signals indicate increasing emphasis on operational deployment and organizational adoption of enterprise AI.",
      "supporting_signal_ids": [
        "signal_001",
        "signal_002"
      ],
      "relevant_career_path_ids": [
        "path_001"
      ],
      "confidence": "medium"
    }
  ],
  "key_developments": [
    {
      "title": "AI expands into a concrete enterprise workflow",
      "summary": "A supplied signal describes AI moving into a specific operational business workflow.",
      "why_it_matters": "This provides concrete evidence that enterprise AI adoption is moving beyond general experimentation.",
      "supporting_signal_ids": [
        "signal_001"
      ],
      "confidence": "medium"
    }
  ],
  "career_implications": [
    {
      "summary": "Implementation and organizational-transformation experience may become increasingly relevant for AI strategy-oriented career paths.",
      "relevant_career_path_ids": [
        "path_001"
      ],
      "supporting_signal_ids": [
        "signal_001",
        "signal_002"
      ],
      "confidence": "medium"
    }
  ],
  "warnings": []
}
```

------------------------------------------------------------
8.1 Exact Nested Object Keys
------------------------------------------------------------

Each Theme must contain exactly:

- `title`;
- `summary`;
- `supporting_signal_ids`;
- `relevant_career_path_ids`;
- `confidence`.

Each Key Development must contain exactly:

- `title`;
- `summary`;
- `why_it_matters`;
- `supporting_signal_ids`;
- `confidence`.

Each Career Implication must contain exactly:

- `summary`;
- `relevant_career_path_ids`;
- `supporting_signal_ids`;
- `confidence`.

`warnings` is an array of non-empty strings. Warnings may describe limitations
such as:

- insufficient evidence for broader Theme synthesis;
- a limited number of current Intelligence signals;
- evidence concentrated in a single organization;
- conflicting supplied signals;
- weak temporal coverage.

Warnings must not become a hidden free-form recommendation or action-plan
section. An empty `warnings` array is valid.

============================================================
9. STRICT VALIDATION REQUIREMENTS
============================================================

Closing Stage 4B must implement strict response validation. At minimum, the
future parser must enforce all of the following:

1. The response is valid JSON and its top-level value is an object.

2. `schema_version` is a string exactly equal to
   `career_intelligence_interpretation_v1`.

3. Top-level keys are exactly `schema_version`, `input_signal_ids`, `themes`,
   `key_developments`, `career_implications`, and `warnings`.

4. All fields have the documented JSON types. Arrays and strings are not
   interchangeable with other values.

5. Every required string is non-empty. A string containing only whitespace is
   not non-empty for contract purposes.

6. Output counts satisfy `themes <= 5`, `key_developments <= 8`, and
   `career_implications <= 5`.

7. Every `confidence` is exactly `high`, `medium`, or `low`.

8. Returned `input_signal_ids` are unique, non-empty strings and have exact set
   equality with the supplied interpretation signal IDs.

9. Every `supporting_signal_id` belongs to `input_signal_ids`.

10. Every `relevant_career_path_id` belongs to the supplied current
    TargetCareerPaths.

11. Every Theme has exactly its documented fields and at least two distinct
    supporting signal IDs.

12. Every Key Development has exactly its documented fields and at least one
    supporting signal ID.

13. Every Career Implication has exactly its documented fields, at least one
    supporting signal ID, and at least one relevant CareerPath ID.

14. Nested ID arrays contain unique, non-empty strings.

15. `warnings` contains only non-empty strings.

16. No unrequested field is accepted inside a Theme, Key Development, or Career
    Implication.

17. The parser performs no silent type coercion.

18. The parser performs no silent repair, ID replacement, field insertion,
    truncation, or normalization of malformed model output.

19. Malformed or contract-invalid LLM output fails explicitly with a dedicated
    interpretation error defined during implementation.

The parser must validate against the actual signal IDs and TargetCareerPath IDs
supplied to the same interpretation request, not merely against JSON shape.

This section documents future implementation requirements only. Closing Stage
4A does not implement the parser.

============================================================
10. PROMPT / PARSER ALIGNMENT
============================================================

The future runtime prompt and parser must describe the same structure exactly.

The exact literal below must appear in the runtime prompt:

```json
"schema_version": "career_intelligence_interpretation_v1"
```

Every JSON example shown in the runtime prompt must itself be a valid concrete
JSON instance accepted by the parser. JSON examples must not contain ambiguous
pseudo-schema values.

Bad:

```json
{
  "confidence": "high | medium | low"
}
```

Good:

```json
{
  "confidence": "medium"
}
```

Allowed alternatives must be explained separately in natural-language prompt
instructions. The prompt must not request fields the parser prohibits, and the
parser must not require fields the prompt omits.

============================================================
11. EMPTY AND INSUFFICIENT EVIDENCE BEHAVIOR
============================================================

No interpretation object is required merely to populate a section.

The following are all valid when the supplied evidence does not support a
defensible output:

```json
"themes": []
```

```json
"key_developments": []
```

```json
"career_implications": []
```

All three arrays may be empty in the same response. The model must not force a
conclusion merely to fill an output quota.

In particular, one isolated signal must not be inflated into a broad trend. A
Theme always requires at least two distinct supporting signals.

Warnings may explain evidence limitations, but warnings do not excuse invalid,
unsupported, or under-grounded interpretation objects.

============================================================
12. NON-GOALS
============================================================

Closing Stage 4 does not:

- search the web;
- acquire new data;
- analyze specific job Opportunities as its primary task;
- redo AI Filter relevance;
- redo CareerSignal normalization;
- redo CareerSignal routing;
- change CareerSignal category;
- regenerate TargetCareerPaths;
- rematch CareerPaths;
- calculate Priority Score;
- change Priority Score;
- produce job-application feasibility;
- create a final user-facing Career Intelligence Brief;
- create final Action Priorities;
- persist interpretation to the database in V1;
- implement scheduler or automation behavior;
- create a new ontology;
- perform unlimited recursive analysis.

Closing Stage 4 also does not produce a Top Opportunities section, final
executive summary, final action plan, or final briefing prose structure.

============================================================
13. CLOSING STAGE BOUNDARIES
============================================================

The Closing Stage responsibility flow is:

```
Closing Stage 3 intelligence bucket
        |
        v
Closing Stage 4 Interpretation
  - Themes
  - Key Developments
  - Career Implications
        |
        v
Closing Stage 5
  Opportunities
  +
  Closing Stage 4 Interpretation
        |
        v
Final user-facing Career Intelligence Brief
```

Closing Stage 4 receives Intelligence signals as its primary content input.
Closing Stage 5 retains responsibility for combining Opportunities with the
Closing Stage 4 Interpretation and producing the final brief.

Closing Stage 4 must not produce:

- a Top Opportunities section;
- a final executive summary;
- a final action plan;
- a final briefing prose structure.

============================================================
14. WORKED CONCEPTUAL EXAMPLE
============================================================

Assume the supplied non-private Intelligence signals state:

Signal A:

Enterprise AI enters a procurement workflow.

Signal B:

Consulting firms expand AI transformation capabilities.

Signal C:

Organizations increase implementation-oriented AI activity.

The three interpretation responsibilities are then distinct:

Theme:

"Enterprise AI is moving toward operational implementation."

This synthesizes the direction collectively supported by multiple changes. It
must cite at least two of Signals A, B, and C.

Key Development:

"AI deployment is entering concrete enterprise workflows."

This describes the concrete change evidenced by Signal A. One signal may be
sufficient for this specific development.

Career Implication:

"AI Strategy and Technology Consulting roles may increasingly value
implementation and organizational-transformation capabilities."

This connects the supplied developments to one or more supplied current
TargetCareerPaths. It must cite at least one supplied signal and at least one
supplied TargetCareerPath ID.

The Key Development describes what concretely changed. The Theme synthesizes
what multiple changes collectively suggest. The Career Implication connects
those changes to current career directions.

============================================================
15. V1 CONTRACT SUMMARY
============================================================

Closing Stage 4 V1 is an Intelligence-first, multi-signal interpretation layer.

Its authoritative input is the Closing Stage 3 `intelligence` bucket together
with current TargetCareerPaths and bounded UserPreferences. The full UserProfile
is omitted by default. PriorityScoreResult remains authoritative upstream
context and is never recalculated or replaced.

Its exact outputs are Themes, Key Developments, Career Implications, and
evidence-limitation warnings under schema version
`career_intelligence_interpretation_v1`.

A Theme requires at least two distinct supplied signals. A Key Development
requires at least one supplied signal. A Career Implication requires at least
one supplied signal and at least one supplied current TargetCareerPath.

All factual external claims must be grounded in supplied Intelligence signals.
TargetCareerPaths and UserPreferences establish user relevance but do not prove
external facts. Unsupported outside knowledge is prohibited, uncertainty must
be preserved, and empty outputs are preferred over forced synthesis.

Closing Stage 4 produces no numeric score, no Opportunity reinterpretation, no
final brief, and no action plan. Closing Stage 5 owns the final user-facing
Career Intelligence Brief.
