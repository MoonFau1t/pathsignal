# AgentWorkflow V1
# Career Intelligence Brief Contract V1

Status: V1 Design Contract

Contract Version:

`career_intelligence_brief_v1`

Purpose:

Define how existing ranked Opportunities and an existing Career Intelligence
Interpretation are assembled into a stable, traceable, machine-readable
CareerIntelligenceBrief for clear user presentation.

This contract belongs to AgentWorkflow V1 Closing Stage 5.

It is the authoritative source of truth for the deterministic brief builder
planned for Closing Stage 5B.

It does NOT implement or authorize:

- a brief builder;
- an LLM call;
- a renderer;
- runtime or pipeline integration;
- database persistence;
- Closing Stage 6 or Closing Stage 7 behavior.

============================================================
1. PURPOSE
============================================================

Closing Stage 5 is a deterministic Presentation / Assembly layer.

Its responsibility is:

```text
existing ranked Opportunities
+
existing Career Intelligence Interpretation
        |
        v
CareerIntelligenceBrief
```

Closing Stage 5 answers:

"How should already-completed career intelligence be organized so the user can
read and understand it clearly?"

It does not answer:

- "Is this information relevant?"
- "How important is this signal?"
- "What does this intelligence mean?"
- "What new career conclusion should be generated?"
- "What should the user do next?"

Relevance filtering, semantic assessment, deterministic priority scoring,
routing, and multi-signal interpretation are authoritative upstream
responsibilities. Action generation is outside V1.

============================================================
2. SCOPE AND RESPONSIBILITY
============================================================

Closing Stage 5 V1 assembles existing runtime objects without changing their
meaning. The assembly path is:

```text
CareerSignalRoutingResult.opportunities
+
CareerIntelligenceInterpretationResult
+
optional current TargetCareerPaths for display-title lookup
+
generated_at
        |
        v
build_career_intelligence_brief(...)
        |
        v
CareerIntelligenceBrief
```

The V1 builder MUST be deterministic for identical explicit inputs, including
the same `generated_at` value.

Closing Stage 5 V1 MUST NOT require or make an LLM call. In particular, it must
not send Opportunities and Interpretation through another model to rewrite a
final brief. Closing Stage 4 has already completed semantic synthesis. A second
semantic layer could distort meaning, omit evidence, change ranking, or
introduce unsupported claims and unnecessary cost.

The builder may only:

- select the fields frozen by this contract;
- copy authoritative upstream values;
- convert runtime enums and immutable tuples into their canonical JSON values;
- resolve supplied CareerPath IDs to supplied display titles;
- represent unavailable display values as JSON `null`;
- place existing content into the frozen section order.

These operations are deterministic presentation mapping, not semantic
analysis.

============================================================
3. INPUTS
============================================================

------------------------------------------------------------
3.1 Ranked Opportunities
------------------------------------------------------------

The authoritative Opportunity input is:

`CareerSignalRoutingResult.opportunities`

This is an ordered tuple of `ScoredCareerSignal` objects. Every item preserves:

- `CareerSignal` in `career_signal`;
- `PriorityAssessmentResult` in `priority_assessment`;
- `PriorityScoreResult` in `priority_score`;
- the upstream `assessment_profile`.

Closing Stage 3 determines which items are Opportunities and establishes their
ordering. Closing Stage 2 supplies their semantic assessment and Priority
Score. Closing Stage 5 must not repeat either responsibility.

------------------------------------------------------------
3.2 Career Intelligence Interpretation
------------------------------------------------------------

The authoritative Intelligence input is:

`CareerIntelligenceInterpretationResult`

Its nested object definitions and semantics remain governed by:

`docs/contracts/career_intelligence_interpretation_v1.md`

Closing Stage 5 copies its:

- `key_developments`;
- `themes`;
- `career_implications`;
- `warnings`.

Closing Stage 5 does not interpret the Stage 4 `input_signal_ids` as a new Brief
section. Those IDs remain the deterministic validation boundary for every
`supporting_signal_id` copied into the Brief.

------------------------------------------------------------
3.3 TargetCareerPaths
------------------------------------------------------------

Current `TargetCareerPath` objects MAY be supplied only as a deterministic
lookup from `path_id` to the existing human-readable `title`.

TargetCareerPaths must not be used to:

- discover a match;
- rematch a signal;
- alter matched-path order;
- score a path;
- generate or rewrite a title;
- perform semantic analysis.

------------------------------------------------------------
3.4 generated_at
------------------------------------------------------------

`generated_at` is the explicit runtime timestamp at which the Brief object is
assembled. It must be a timezone-aware ISO 8601 string following existing
project timestamp conventions.

The assembly boundary should capture this value once. The builder must copy it
unchanged after deterministic validation. `generated_at` is presentation
metadata only and must not be used to recompute recency, Priority Score, Tier,
ordering, or interpretation confidence.

------------------------------------------------------------
3.5 Excluded Inputs
------------------------------------------------------------

Closing Stage 5 does not require:

- UserProfile;
- full UserPreferences;
- RawItems;
- SourceItems;
- SearchPlans;
- SearchQueries.

============================================================
4. CAREERINTELLIGENCEBRIEF
============================================================

The canonical machine-readable Brief has exactly these top-level keys:

1. `schema_version`
2. `generated_at`
3. `opportunities`
4. `key_developments`
5. `themes`
6. `career_implications`
7. `warnings`

`schema_version` must equal exactly:

`career_intelligence_brief_v1`

No extra top-level fields are allowed. In particular, V1 does not contain:

- `executive_summary`;
- `recommendations`;
- `action_priorities`;
- `next_steps`;
- `overall_score`;
- `market_summary`;
- `best_opportunity`.

The canonical machine object always contains every fixed section, even when a
section is empty. A later visual renderer may omit an empty section from the
display, but it must not alter the canonical Brief.

============================================================
5. OPPORTUNITY PRESENTATION CONTRACT
============================================================

------------------------------------------------------------
5.1 Exact Opportunity Fields
------------------------------------------------------------

Every Opportunity presentation object contains exactly:

- `signal_id`;
- `title`;
- `organization`;
- `summary`;
- `url`;
- `published_at`;
- `priority_score`;
- `priority_tier`;
- `matched_career_paths`;
- `user_policy_fit_reason`;
- `opportunity_feasibility_reason`.

No additional display score, recommendation status, rank score, or rewritten
semantic text is allowed.

------------------------------------------------------------
5.2 Deterministic Live-Model Mapping
------------------------------------------------------------

For one upstream `ScoredCareerSignal` named `scored`, the mapping is:

| Brief field | Authoritative runtime source |
|---|---|
| `signal_id` | `scored.career_signal.signal_id` |
| `title` | `scored.career_signal.title` |
| `organization` | `scored.career_signal.organization` |
| `summary` | `scored.career_signal.summary` |
| `url` | `scored.career_signal.url` |
| `published_at` | `scored.career_signal.published_at` |
| `priority_score` | `scored.priority_score.priority_score` |
| `priority_tier` | `scored.priority_score.tier.value` |
| matched path IDs | `scored.priority_score.matched_path_ids` |
| `user_policy_fit_reason` | `scored.priority_assessment.components["user_policy_fit"].reason` when available |
| `opportunity_feasibility_reason` | `scored.priority_assessment.components["opportunity_feasibility"].reason` when available |

The builder must validate that the CareerSignal, PriorityAssessmentResult, and
PriorityScoreResult identify the same `signal_id`, and that the item has the
Opportunity assessment profile. A mismatch is a data-integrity error, not a
presentation warning.

------------------------------------------------------------
5.3 Priority Authority and Ordering
------------------------------------------------------------

`priority_score` and `priority_tier` are copied exactly from
`PriorityScoreResult`. The builder must not:

- calculate a display score;
- round or rescale the canonical score;
- create percentage fit;
- change a Tier;
- calculate an Opportunity rank score;
- label an item recommended or not recommended.

The order of `CareerSignalRoutingResult.opportunities` is authoritative and
must be preserved exactly. Closing Stage 5 may not sort, rerank, group, or
otherwise reorder Opportunities.

------------------------------------------------------------
5.4 Matched CareerPath Presentation
------------------------------------------------------------

`matched_career_paths` preserves the order and identity of
`PriorityScoreResult.matched_path_ids`. Each path is represented as exactly:

```json
{
  "path_id": "ai_strategy_associate",
  "title": "AI Strategy Associate"
}
```

`title` is copied from the supplied current `TargetCareerPath` with the same
`path_id`.

If an upstream matched path ID cannot be resolved:

- preserve the path ID in its original position;
- set `title` to JSON `null`;
- do not invent a title;
- do not silently drop the path;
- do not add a semantic warning.

Duplicate supplied TargetCareerPath IDs are a data-integrity error. The builder
must not choose one nondeterministically.

------------------------------------------------------------
5.5 Opportunity Assessment Reasons
------------------------------------------------------------

The two reason fields expose existing Stage 2 semantic text without rewriting.

When the corresponding `SemanticComponentResult.status` is `available`, copy
its `reason` exactly.

When the corresponding status is `unavailable`, emit JSON `null`. The builder
must not convert the unavailable component's technical explanation into a new
user-facing semantic reason.

Missing required Opportunity components, an unexpected component shape, or an
assessment profile mismatch is a data-integrity error. The builder must not
silently repair it.

============================================================
6. INTELLIGENCE PRESENTATION CONTRACT
============================================================

Closing Stage 4 output is authoritative. Closing Stage 5 converts the frozen
runtime objects to their existing `to_dict()` values without changing field
meaning, text, confidence, IDs, array order, or evidence relationships.

The exact Stage 4 nested semantics remain defined by
`career_intelligence_interpretation_v1`.

------------------------------------------------------------
6.1 Key Developments
------------------------------------------------------------

Copy `CareerIntelligenceInterpretationResult.key_developments` in its existing
order. Preserve exactly:

- `title`;
- `summary`;
- `why_it_matters`;
- `supporting_signal_ids`;
- `confidence`.

------------------------------------------------------------
6.2 Themes
------------------------------------------------------------

Copy `CareerIntelligenceInterpretationResult.themes` in its existing order.
Preserve exactly:

- `title`;
- `summary`;
- `supporting_signal_ids`;
- `relevant_career_path_ids`;
- `confidence`.

Closing Stage 5 must not create, merge, split, rewrite, or reprioritize a Theme.

------------------------------------------------------------
6.3 Career Implications
------------------------------------------------------------

Copy `CareerIntelligenceInterpretationResult.career_implications` in its
existing order. Preserve exactly:

- `summary`;
- `relevant_career_path_ids`;
- `supporting_signal_ids`;
- `confidence`.

Closing Stage 5 must not generate an additional Career Implication.

------------------------------------------------------------
6.4 Warnings
------------------------------------------------------------

Copy `CareerIntelligenceInterpretationResult.warnings` in original order.

V1 warnings contain only the authoritative Stage 4 interpretation warning
strings. The builder must not:

- suppress them to make the Brief look cleaner;
- rewrite them;
- turn them into recommendations;
- append technical presentation problems;
- generate new semantic warnings.

Unresolved path titles and other technical assembly conditions follow the
explicit mapping or validation rules in this contract. They do not enter the
semantic warnings array.

============================================================
7. ORDERING
============================================================

The canonical and default user reading order is:

1. Opportunities
2. Key Developments
3. Themes
4. Career Implications
5. Warnings

The V1 product rationale is:

```text
What opportunities exist?
        |
        v
What important developments happened?
        |
        v
What broader themes are emerging?
        |
        v
What might those developments mean for my career paths?
```

Opportunities come first because they are immediately inspectable career
options. Intelligence then provides broader external context and career-path
relevance.

Section ordering is a presentation decision only. It does not create semantic
priority between Stage 4 objects. Within every section, preserve upstream
order exactly.

============================================================
8. EMPTY AND NULLABLE BEHAVIOR
============================================================

------------------------------------------------------------
8.1 Empty Sections
------------------------------------------------------------

All fixed arrays are always present:

```json
{
  "opportunities": [],
  "key_developments": [],
  "themes": [],
  "career_implications": [],
  "warnings": []
}
```

The builder must not manufacture content to fill an empty section.

A visual renderer may omit an empty section heading, but the canonical
machine-readable object remains unchanged.

------------------------------------------------------------
8.2 Nullable Opportunity Fields
------------------------------------------------------------

The stable Opportunity object retains every frozen key.

`published_at` is explicitly optional in the live `CareerSignal` model and is
serialized as JSON `null` when absent.

`organization`, `summary`, and `url` are required string fields in the live
dataclass, but the model does not enforce that they are non-empty and upstream
source data may legitimately lack a useful value. Their Brief fields therefore
allow `string` or JSON `null`.

For `organization`, `summary`, and `url`:

- copy a non-empty upstream string exactly;
- emit JSON `null` when the value is `None`, empty, or whitespace-only;
- do not fabricate `"Unknown Company"`, `"N/A"`, or similar placeholders.

`user_policy_fit_reason` and `opportunity_feasibility_reason` allow `string` or
JSON `null` according to Section 5.5.

An unresolved matched CareerPath `title` is JSON `null` according to Section
5.4.

`signal_id`, Opportunity `title`, `priority_score`, `priority_tier`, and matched
path `path_id` are not nullable. Invalid required values cause deterministic
validation failure.

============================================================
9. CANONICAL JSON SCHEMA
============================================================

The canonical logical JSON Schema is:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "CareerIntelligenceBrief",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "schema_version",
    "generated_at",
    "opportunities",
    "key_developments",
    "themes",
    "career_implications",
    "warnings"
  ],
  "properties": {
    "schema_version": {
      "const": "career_intelligence_brief_v1"
    },
    "generated_at": {
      "type": "string",
      "format": "date-time"
    },
    "opportunities": {
      "type": "array",
      "items": {
        "$ref": "#/$defs/opportunity"
      }
    },
    "key_developments": {
      "type": "array",
      "items": {
        "$ref": "#/$defs/key_development"
      }
    },
    "themes": {
      "type": "array",
      "items": {
        "$ref": "#/$defs/theme"
      }
    },
    "career_implications": {
      "type": "array",
      "items": {
        "$ref": "#/$defs/career_implication"
      }
    },
    "warnings": {
      "type": "array",
      "items": {
        "type": "string",
        "minLength": 1
      }
    }
  },
  "$defs": {
    "nullable_string": {
      "type": [
        "string",
        "null"
      ],
      "minLength": 1
    },
    "matched_career_path": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "path_id",
        "title"
      ],
      "properties": {
        "path_id": {
          "type": "string",
          "minLength": 1
        },
        "title": {
          "$ref": "#/$defs/nullable_string"
        }
      }
    },
    "opportunity": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "signal_id",
        "title",
        "organization",
        "summary",
        "url",
        "published_at",
        "priority_score",
        "priority_tier",
        "matched_career_paths",
        "user_policy_fit_reason",
        "opportunity_feasibility_reason"
      ],
      "properties": {
        "signal_id": {
          "type": "string",
          "minLength": 1
        },
        "title": {
          "type": "string",
          "minLength": 1
        },
        "organization": {
          "$ref": "#/$defs/nullable_string"
        },
        "summary": {
          "$ref": "#/$defs/nullable_string"
        },
        "url": {
          "$ref": "#/$defs/nullable_string"
        },
        "published_at": {
          "$ref": "#/$defs/nullable_string"
        },
        "priority_score": {
          "type": "number",
          "minimum": 0,
          "maximum": 100
        },
        "priority_tier": {
          "enum": [
            "high",
            "medium_high",
            "medium",
            "low"
          ]
        },
        "matched_career_paths": {
          "type": "array",
          "items": {
            "$ref": "#/$defs/matched_career_path"
          }
        },
        "user_policy_fit_reason": {
          "$ref": "#/$defs/nullable_string"
        },
        "opportunity_feasibility_reason": {
          "$ref": "#/$defs/nullable_string"
        }
      }
    },
    "confidence": {
      "enum": [
        "high",
        "medium",
        "low"
      ]
    },
    "signal_ids": {
      "type": "array",
      "items": {
        "type": "string",
        "minLength": 1
      },
      "uniqueItems": true
    },
    "path_ids": {
      "type": "array",
      "items": {
        "type": "string",
        "minLength": 1
      },
      "uniqueItems": true
    },
    "key_development": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "title",
        "summary",
        "why_it_matters",
        "supporting_signal_ids",
        "confidence"
      ],
      "properties": {
        "title": {
          "type": "string",
          "minLength": 1
        },
        "summary": {
          "type": "string",
          "minLength": 1
        },
        "why_it_matters": {
          "type": "string",
          "minLength": 1
        },
        "supporting_signal_ids": {
          "$ref": "#/$defs/signal_ids"
        },
        "confidence": {
          "$ref": "#/$defs/confidence"
        }
      }
    },
    "theme": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "title",
        "summary",
        "supporting_signal_ids",
        "relevant_career_path_ids",
        "confidence"
      ],
      "properties": {
        "title": {
          "type": "string",
          "minLength": 1
        },
        "summary": {
          "type": "string",
          "minLength": 1
        },
        "supporting_signal_ids": {
          "$ref": "#/$defs/signal_ids"
        },
        "relevant_career_path_ids": {
          "$ref": "#/$defs/path_ids"
        },
        "confidence": {
          "$ref": "#/$defs/confidence"
        }
      }
    },
    "career_implication": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "summary",
        "relevant_career_path_ids",
        "supporting_signal_ids",
        "confidence"
      ],
      "properties": {
        "summary": {
          "type": "string",
          "minLength": 1
        },
        "relevant_career_path_ids": {
          "$ref": "#/$defs/path_ids"
        },
        "supporting_signal_ids": {
          "$ref": "#/$defs/signal_ids"
        },
        "confidence": {
          "$ref": "#/$defs/confidence"
        }
      }
    }
  }
}
```

The schema above freezes machine shape. The minimum supporting-signal counts,
CareerPath grounding, confidence semantics, and other semantic invariants for
the three Intelligence objects remain authoritative in
`career_intelligence_interpretation_v1`; Closing Stage 5 does not define a
second competing interpretation contract.

------------------------------------------------------------
9.1 Concrete Valid Brief
------------------------------------------------------------

```json
{
  "schema_version": "career_intelligence_brief_v1",
  "generated_at": "2026-08-12T12:00:00+08:00",
  "opportunities": [
    {
      "signal_id": "signal_001",
      "title": "AI Strategy Associate",
      "organization": "Example Company",
      "summary": "Support AI strategy and transformation initiatives.",
      "url": "https://example.com/jobs/001",
      "published_at": "2026-08-11T10:00:00+00:00",
      "priority_score": 86.5,
      "priority_tier": "high",
      "matched_career_paths": [
        {
          "path_id": "ai_strategy_associate",
          "title": "AI Strategy Associate"
        }
      ],
      "user_policy_fit_reason": "The role aligns with the supplied career preferences and work-content priorities.",
      "opportunity_feasibility_reason": "The explicit experience requirements are compatible with the assessed career stage."
    }
  ],
  "key_developments": [
    {
      "title": "AI expands into operational workflows",
      "summary": "A supplied signal describes AI entering a concrete enterprise workflow.",
      "why_it_matters": "The development provides a concrete example of operational enterprise AI adoption.",
      "supporting_signal_ids": [
        "signal_101"
      ],
      "confidence": "medium"
    }
  ],
  "themes": [
    {
      "title": "Enterprise AI moves toward operational implementation",
      "summary": "Multiple supplied signals indicate increasing emphasis on operational deployment.",
      "supporting_signal_ids": [
        "signal_101",
        "signal_102"
      ],
      "relevant_career_path_ids": [
        "ai_strategy_associate",
        "digital_transformation_consultant"
      ],
      "confidence": "medium"
    }
  ],
  "career_implications": [
    {
      "summary": "Operational AI implementation is relevant to career paths focused on AI strategy and digital transformation.",
      "relevant_career_path_ids": [
        "ai_strategy_associate",
        "digital_transformation_consultant"
      ],
      "supporting_signal_ids": [
        "signal_101",
        "signal_102"
      ],
      "confidence": "medium"
    }
  ],
  "warnings": []
}
```

This is concrete valid JSON. It contains no placeholders, pseudo-schema union
values, Markdown links, or fields outside the frozen V1 object shapes.

============================================================
10. TRACEABILITY
============================================================

The canonical Brief retains enough identity to trace every presentation object
to authoritative upstream data.

At minimum:

- every Opportunity retains `signal_id`;
- every Intelligence object retains `supporting_signal_ids`;
- Theme and Career Implication path relevance retains path IDs;
- every matched Opportunity path retains `path_id` even when its title cannot
  be resolved.

A renderer may visually de-emphasize internal IDs, but it must consume the
canonical object without removing those IDs from that object.

============================================================
11. DETERMINISTIC ASSEMBLY RULES
============================================================

Closing Stage 5B must follow these rules:

1. Require `CareerSignalRoutingResult`,
   `CareerIntelligenceInterpretationResult`, optional current
   `TargetCareerPaths`, and explicit `generated_at`.

2. Read only `CareerSignalRoutingResult.opportunities`. Ignore neither item nor
   order, and never present `intelligence` or `unrouted` as Opportunities.

3. Preserve Opportunity order exactly.

4. Insert each upstream Opportunity exactly once.

5. Copy canonical CareerSignal fields according to Section 5.2.

6. Copy `priority_score` and `priority_tier` exactly.

7. Preserve `matched_path_ids` identity and order, using TargetCareerPaths only
   for title lookup.

8. Copy available Opportunity assessment reasons exactly and represent
   unavailable components as `null`.

9. Copy Key Developments, Themes, Career Implications, and warnings exactly and
   in upstream order.

10. Construct every fixed top-level section even when empty.

11. Set the schema version to exactly `career_intelligence_brief_v1`.

12. Copy the validated timezone-aware `generated_at` value without using it for
    any upstream calculation.

13. Perform no I/O other than returning the assembled runtime object. The V1
    builder makes no network call and no database write.

============================================================
12. VALIDATION REQUIREMENTS
============================================================

Closing Stage 5B must validate assembly invariants deterministically. At
minimum:

1. The schema version is exact.

2. Top-level and nested fields match this contract exactly.

3. `generated_at` is a valid timezone-aware timestamp.

4. Every Opportunity input is a `ScoredCareerSignal` from the authoritative
   `opportunities` tuple.

5. CareerSignal, PriorityAssessmentResult, and PriorityScoreResult signal IDs
   agree.

6. Opportunity assessment profiles and component names match the frozen Stage
   2 Opportunity contract.

7. Priority Score and Tier equal their upstream values exactly.

8. Opportunity order equals upstream order exactly.

9. No Opportunity is inserted twice.

10. Matched CareerPath IDs equal upstream `matched_path_ids` in the same order.

11. CareerPath titles, when present, come only from supplied TargetCareerPaths.

12. Interpretation text, IDs, confidence, object order, and warnings equal the
    Stage 4 input exactly.

13. Every fixed section exists and has the correct type.

14. No prohibited summary, recommendation, action, or score field exists.

Validation must not perform LLM-style semantic review, repair malformed input,
or silently regenerate missing upstream content. Programming and data-integrity
problems fail explicitly rather than becoming user-facing semantic warnings.

============================================================
13. USER-FACING RENDERING PRINCIPLES
============================================================

The canonical Brief governs content, identity, and ordering. It does not freeze
visual styling.

A later deterministic renderer may use:

- headings;
- spacing;
- clickable links;
- score and Tier formatting;
- omission of empty visual sections;
- de-emphasis of internal traceability IDs.

A renderer must not:

- mutate the canonical Brief;
- rewrite upstream text;
- hide interpretation warnings by default merely for aesthetics;
- change Opportunity order;
- add semantic labels or recommendations;
- group content by acquisition source type.

The Brief is organized by product meaning:

- Opportunity;
- Key Development;
- Theme;
- Career Implication.

It is not organized by RSS, Search API, or Selected Website.

============================================================
14. NON-GOALS
============================================================

Closing Stage 5 V1 does NOT:

- acquire data;
- search the web;
- call DeepSeek or any other LLM;
- perform AI Filter;
- normalize CareerSignals;
- deduplicate SourceItems;
- score CareerSignals;
- change Priority Scores or Tiers;
- reroute CareerSignals;
- interpret multiple Intelligence signals;
- generate Themes;
- generate Key Developments;
- generate Career Implications;
- evaluate applicant qualifications;
- rematch CareerPaths;
- generate Action Priorities;
- generate recommendations;
- generate an executive summary;
- paraphrase upstream content;
- persist the Brief in V1;
- add a Brief database table or repository;
- implement notifications;
- implement scheduling;
- design a web UI.

Action fields such as `apply now`, `contact recruiter`, `research company`,
`learn skill`, `follow up`, `monitor company`, and `prepare interview` are not
part of this contract.

Priority Score and Tier are sufficient for V1 Opportunity presentation. The
Brief must not label an Opportunity `recommended`, `strongly recommended`,
`apply immediately`, or `avoid` unless a future upstream contract creates an
authoritative semantic object for that purpose.

============================================================
15. CLOSING STAGE BOUNDARIES
============================================================

Closing Stage 4 asks:

"What do the Intelligence signals mean?"

Closing Stage 5 asks:

"How do we present existing Opportunities and Intelligence clearly?"

Closing Stage 6 asks:

"Does the entire V1 system work end to end under manual validation?"

Closing Stage 7 asks:

"How do we seal, document, and present the V1 project?"

Closing Stage 5 must not absorb semantic interpretation from Stage 4, manual
end-to-end acceptance from Stage 6, or final project sealing and presentation
from Stage 7.

Closing Stage 5A freezes this contract only. Closing Stage 5B may implement the
deterministic runtime builder in a separate reviewed task.

============================================================
16. V1 CONTRACT SUMMARY
============================================================

Closing Stage 5 V1 is a deterministic Presentation / Assembly layer. It
combines authoritative ranked Opportunities from Closing Stage 3 with the
authoritative Career Intelligence Interpretation from Closing Stage 4.

It copies Opportunity identity, source presentation fields, Priority Score,
Tier, matched CareerPaths, and available Stage 2 reasons without semantic
change. It preserves upstream Opportunity order. Current TargetCareerPaths may
provide display titles only; unresolved IDs remain visible with a `null` title.

It copies Key Developments, Themes, Career Implications, and interpretation
warnings without rewriting or reordering them. The canonical machine object
always includes all fixed sections and traceability IDs. Empty arrays and
nullable display fields represent unavailable content without fabrication.

The V1 Brief requires no LLM, network call, new scoring, rerouting,
reinterpretation, recommendation layer, executive summary, Action Priorities,
source-type presentation logic, or database persistence.

The expected Closing Stage 5B implementation is therefore:

```text
career_signal_routing.opportunities
+
career_intelligence_interpretation
+
TargetCareerPath label lookup
+
generated_at
        |
        v
build_career_intelligence_brief(...)
        |
        v
CareerIntelligenceBrief
```

No LLM. No network call. No DB write. No semantic analysis.
