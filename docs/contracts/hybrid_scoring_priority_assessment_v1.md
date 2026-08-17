# AgentWorkflow V1
# Hybrid Scoring / Priority Assessment Contract v1

Status: V1 Design Contract

Contract Version:

`hybrid_priority_assessment_v1`

Purpose:

Define how an already-accepted CareerSignal is semantically assessed and
deterministically scored for user priority.

This contract belongs to AgentWorkflow V1 Closing Stage 2.

It does NOT define:

- upstream AI relevance filtering;
- CareerSignal normalization;
- CareerSignal routing/grouping;
- cross-signal LLM interpretation;
- Final Brief generation;
- score persistence.

============================================================
1. CORE PRODUCT QUESTION
============================================================

The upstream AI Filter answers:

"Is this information sufficiently relevant to retain?"

Closing Stage 2 answers:

"Among retained CareerSignals, how much attention should the current user give
this signal now?"

These are different questions.

`CareerSignal.relevance_score` remains upstream AI Filter confidence.

It must NOT be redefined as downstream priority.

The new contextual result is:

`priority_score`

Priority is conceptually:

```
priority =
f(
    CareerSignal,
    current planning context,
    semantic assessment,
    deterministic evidence,
    scoring policy,
    as_of time
)
```

Priority is NOT treated as an intrinsic permanent property of CareerSignal.

============================================================
2. HYBRID DESIGN PRINCIPLE
============================================================

AgentWorkflow uses a hybrid scoring architecture.

AI is responsible for semantic judgments that require understanding
natural-language evidence.

Deterministic rules are responsible for:

- structured calculations;
- weighting;
- normalization;
- missing-data handling;
- tiers;
- final Priority Score calculation.

The LLM must never directly determine the final Priority Score.

Architecture:

```
CareerSignal
+
Current User Context
+
Supporting Source Evidence
        |
        v
AI Semantic Assessment
        +
Deterministic Evidence
        |
        v
Versioned Scoring Policy
        |
        v
PriorityScoreResult
```

============================================================
3. TWO SCORING PROFILES
============================================================

V1 defines two priority profiles:

1. `opportunity`
2. `intelligence`

This profile distinction exists for scoring only.

It must NOT be treated as the complete Closing Stage 3 routing/grouping
implementation.

Source type must never determine the semantic profile by itself.

============================================================
4. OPPORTUNITY PRIORITY MODEL
============================================================

Question:

"How much should the user prioritize this career opportunity?"

Components:

| Component | Weight | Owner |
|---|---:|---|
| Path Alignment | 30 | Deterministic / upstream AI evidence |
| User Policy Fit | 25 | AI Semantic Assessment |
| Opportunity Feasibility | 20 | AI Semantic Assessment |
| Recency | 15 | Deterministic |
| Source Provenance | 5 | Deterministic when evidence exists |
| AI Confidence | 5 | Upstream AI Filter result |

Total configured weight: 100.

Interpretation:

75% of configured Opportunity priority concerns:

- career-direction alignment;
- user preference fit;
- realistic attainability.

The remaining 25% concerns:

- timeliness;
- source evidence;
- upstream relevance confidence.

============================================================
5. INTELLIGENCE PRIORITY MODEL
============================================================

Question:

"How much should the user pay attention to this career-intelligence signal?"

Components:

| Component | Weight | Owner |
|---|---:|---|
| Career Relevance Strength | 25 | AI Semantic Assessment |
| Signal Significance | 25 | AI Semantic Assessment |
| Path Alignment | 20 | Deterministic / upstream AI evidence |
| Recency | 15 | Deterministic |
| Source Provenance | 10 | Deterministic when evidence exists |
| AI Confidence | 5 | Upstream AI Filter result |

Total configured weight: 100.

The largest share of Intelligence priority concerns:

- whether the specific development materially matters to the user's career
  planning;
- whether the signal itself represents substantive evidence.

============================================================
6. AI SEMANTIC SCORE SCALE
============================================================

LLM-generated semantic assessments may use ONLY:

- `1.00`
- `0.75`
- `0.50`
- `0.25`
- `0.00`
- `unavailable`

Arbitrary intermediate values such as:

`0.68`
`0.81`
`0.93`

are prohibited.

The LLM is performing rubric-based semantic classification, not precise
quantitative measurement.

`unavailable` means evidence is insufficient.

Missing evidence must never automatically become:

- `0.50`;
- `0.00`;
- or any other numeric score.

============================================================
7. UNIVERSAL AI ASSESSMENT RULES
============================================================

All semantic assessment dimensions follow these rules.

1. Missing evidence is not negative evidence.

2. Use only evidence supplied in the assessment request.

3. Do not fill missing item facts using unsupported external assumptions.

4. Every available score must include:
   - score;
   - reason;
   - supporting evidence.

5. `unavailable` must include:
   - score = null;
   - reason explaining why evidence is insufficient.

6. AI must not calculate the final Priority Score.

7. AI must not change the upstream relevance decision.

8. AI must not redo basic CareerPath matching.

9. AI must distinguish explicit evidence from inference.

10. Unknown information must not be treated as a negative signal.

============================================================
8. OPPORTUNITY SEMANTIC DIMENSION:
   USER POLICY FIT
============================================================

Definition:

How well the actual characteristics of the opportunity align with the user's
explicitly stated career preferences, positive preferences, exclusions, and
constraints.

This dimension may consider supplied evidence related to:

- work content;
- industry;
- business model;
- role characteristics;
- seniority preference;
- location preference;
- work-environment preference;
- explicit user exclusions;
- other relevant UserPreferences.

This dimension does NOT evaluate:

- whether the user is qualified;
- CareerPath priority;
- recency;
- source quality;
- company prestige based on outside knowledge.

Rubric:

### 1.00 - Very Strong Fit

Clear evidence of strong alignment with multiple important positive preferences
and no material evidenced conflict.

### 0.75 - Strong Fit

Overall clearly aligned with user preferences, with only minor concerns,
incomplete secondary information, or limited uncertainty.

### 0.50 - Mixed Fit

Meaningful positive alignment exists, but there is also meaningful evidenced
conflict or tension.

Uncertainty alone must NOT produce 0.50.

### 0.25 - Weak Fit / Material Conflict

Career relevance remains, but multiple important user-preference conflicts exist,
or one substantial evidenced conflict materially reduces fit.

### 0.00 - Direct Policy Conflict

Clear opportunity evidence directly conflicts with an explicit user hard
preference or exclusion.

This score affects the normal weighted scoring calculation.

It does NOT trigger a separate final-score override or cap.

### unavailable

The supplied opportunity evidence is insufficient to make a defensible
user-policy judgment.

============================================================
9. OPPORTUNITY SEMANTIC DIMENSION:
   OPPORTUNITY FEASIBILITY
============================================================

Definition:

How realistically attainable this opportunity is for the user based on explicit
opportunity requirements and supplied user background.

Possible evidence may include:

- years of experience;
- seniority;
- required skills;
- education;
- qualifications;
- eligibility requirements;
- work authorization;
- explicit location requirements.

This dimension evaluates reasonable application feasibility.

It does NOT estimate:

- probability of receiving an offer;
- user preference;
- career-path importance;
- company prestige;
- long-term career upside.

Rubric:

### 1.00 - Clearly Attainable

Evidence places the opportunity clearly within the user's normal competitive
range, with no material qualification gap.

### 0.75 - Attainable With Manageable Gaps

Broadly realistic, with minor or manageable gaps.

### 0.50 - Meaningful Stretch but Plausible

The opportunity represents a meaningful stretch but remains realistically
contestable.

The user's existing experience-tolerance policy may be reflected when explicit
role experience evidence exists.

### 0.25 - Major Gap

Explicit evidence shows substantial qualification, experience, seniority, or
eligibility gaps.

The opportunity may technically remain possible, but current feasibility is low.

### 0.00 - Explicitly Implausible / Ineligible

Clear supplied evidence establishes a major incompatibility or explicit
ineligibility.

### unavailable

Opportunity requirements are insufficiently stated.

Job title alone is not sufficient evidence for qualification requirements.

============================================================
10. INTELLIGENCE SEMANTIC DIMENSION:
    CAREER RELEVANCE STRENGTH
============================================================

Definition:

How directly and materially the specific development matters to the user's
current career directions.

This differs from Path Alignment.

Path Alignment answers:

"Which TargetCareerPath does this signal relate to, and how strong is that path
for the user?"

Career Relevance Strength answers:

"How much does this specific event actually matter for that career direction?"

Relevant evidence may concern:

- hiring demand;
- role creation;
- capability expansion;
- organizational strategy;
- investment direction;
- market demand;
- business adoption;
- talent structure;
- other concrete career-market implications.

Rubric:

### 1.00 - Direct and Material Career Relevance

The development directly and materially affects one or more important current
career directions.

### 0.75 - Clear Career Relevance

The development has clear and meaningful career implications, although the
connection is less direct or less consequential.

### 0.50 - Indirect but Useful

A defensible career implication exists but requires an additional reasoning
step.

### 0.25 - Peripheral

The topic overlaps with the user's career interests, but the specific event has
weak practical career meaning.

### 0.00 - No Material Career Meaning

Further assessment finds essentially no substantive career value, despite the
item having passed the broader upstream filter.

### unavailable

The supplied evidence is insufficient to determine career meaning.

============================================================
11. INTELLIGENCE SEMANTIC DIMENSION:
    SIGNAL SIGNIFICANCE
============================================================

Definition:

How substantive, concrete, and information-rich this individual signal is
independent of the user's personal fit.

This dimension evaluates the signal itself.

It does NOT evaluate whether the user personally cares about it.

Rubric:

### 1.00 - Major Concrete Development

A clear and substantial event or change, such as:

- major expansion;
- major investment;
- significant funding;
- acquisition;
- new business unit;
- major strategic shift;
- major hiring initiative;
- substantial organizational restructuring;
- major new capability/practice.

### 0.75 - Specific Meaningful Development

A concrete, meaningful development with real informational value, but below the
highest level of materiality.

### 0.50 - Moderate / Incremental Signal

Real and useful information representing a moderate or incremental development.

### 0.25 - Weak / Generic Signal

Mostly generic commentary, promotional content, ordinary thought leadership,
vague optimism, or low-information material.

### 0.00 - Essentially No Intelligence Value

The item provides essentially no substantive intelligence evidence.

### unavailable

The supplied content is insufficient to determine what actually occurred.

Important boundary:

This dimension evaluates ONE signal only.

It must NOT infer broader trends from multiple signals.

Cross-signal synthesis belongs to downstream LLM Interpretation.

============================================================
12. ASSESSMENT INPUT CONTRACT
============================================================

Priority Assessment operates on an already accepted CareerSignal.

The runtime should provide the minimum relevant information needed for semantic
assessment.

------------------------------------------------------------
12.1 Common Control Fields
------------------------------------------------------------

Required:

- `assessment_profile`
  - `opportunity`
  - `intelligence`

- `signal_id`

- `as_of`

`as_of` is used by deterministic scoring, not semantic inference.

------------------------------------------------------------
12.2 CareerSignal Payload
------------------------------------------------------------

Provide:

- `signal_id`
- `title`
- `organization`
- `url`
- `category`
- `summary`
- `published_at`
- selected relevant metadata

`CareerSignal.relevance_score` remains part of the canonical CareerSignal
runtime context and represents upstream AI Filter confidence. To avoid
anchoring the Stage 2B semantic assessment, it must be omitted from the
semantic LLM payload. It remains available to deterministic Closing Stage 2C
scoring.

Do not blindly send unrelated metadata.

------------------------------------------------------------
12.3 Matched TargetCareerPaths
------------------------------------------------------------

Only TargetCareerPaths already matched upstream should normally be supplied.

Provide, where available:

- `path_id`
- `title`
- `description`
- `fit_score`
- `path_type`
- relevant constraint/risk context if already structured

Do not ask Priority Assessment to discover new CareerPath matches.

------------------------------------------------------------
12.4 UserProfile Payload
------------------------------------------------------------

For Opportunity assessment, provide only fields relevant to feasibility and
preference comparison.

Expected logical content includes:

- background summary;
- skills;
- education/background evidence where structured;
- experience evidence where structured;
- preferred roles where useful;
- preferred locations where useful;
- explicit constraints.

Exact field names must follow the live UserProfile model.

Do not create fictional fields merely to satisfy this contract.

For Intelligence assessment, UserProfile should be omitted unless a concrete
semantic need is demonstrated.

------------------------------------------------------------
12.5 UserPreferences Payload
------------------------------------------------------------

Supply only preference categories relevant to the assessment.

Expected logical content includes currently available structures such as:

- hard constraints;
- soft preferences;
- location preferences;
- seniority preferences;
- experience requirement tolerance;
- career / industry preferences;
- business-model exclusions;
- work-environment preferences / risk policy;
- compensation policy where relevant.

Exact runtime keys must be resolved against the current live UserPreferences
artifact.

UserPreferences is currently flexible JSON.

Implementation must not invent semantics for missing keys.

------------------------------------------------------------
12.6 Supporting Source Evidence
------------------------------------------------------------

Priority Assessment may receive supporting source evidence when the normalized
CareerSignal does not contain enough information for a defensible semantic
judgment.

Expected fields:

- title;
- organization;
- url;
- published_at;
- raw_text;
- selected relevant metadata.

Raw source evidence exists to support judgments such as:

- explicit years-of-experience requirements;
- role responsibilities;
- required qualifications;
- location requirements;
- concrete event details.

The CareerSignal remains the canonical signal object.

Supporting evidence does not create a second CareerSignal.

------------------------------------------------------------
12.7 Upstream FilterDecision
------------------------------------------------------------

The deterministic scorer may consume:

- confidence;
- matched_career_path_ids;
- suggested category.

The semantic assessment LLM should generally NOT receive upstream AI confidence
or upstream reasoning unless a concrete need is demonstrated.

Reason:

Avoid unnecessary anchoring of the second semantic assessment to the first AI
model judgment.

============================================================
13. OPPORTUNITY ASSESSMENT INPUT SHAPE
============================================================

Conceptual input:

```json
{
  "assessment_profile": "opportunity",
  "signal_id": "...",
  "as_of": "...",
  "user_profile": {},
  "user_preferences": {},
  "matched_target_career_paths": [],
  "career_signal": {},
  "supporting_source_evidence": {}
}
```

============================================================
14. INTELLIGENCE ASSESSMENT INPUT SHAPE
============================================================

Conceptual input:

```json
{
  "assessment_profile": "intelligence",
  "signal_id": "...",
  "as_of": "...",
  "user_preferences": {},
  "matched_target_career_paths": [],
  "career_signal": {},
  "supporting_source_evidence": {}
}
```

UserProfile is omitted by default.

============================================================
15. AI ASSESSMENT OUTPUT CONTRACT
============================================================

All AI assessments return structured JSON.

Common fields:

- `schema_version`
- `signal_id`
- `assessment_profile`
- `components`
- `warnings`

No overall Priority Score is allowed.

============================================================
16. OUTPUT JSON SCHEMA
============================================================

Canonical logical schema:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "PriorityAssessmentResult",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "schema_version",
    "signal_id",
    "assessment_profile",
    "components",
    "warnings"
  ],
  "properties": {
    "schema_version": {
      "const": "priority_assessment_v1"
    },
    "signal_id": {
      "type": "string",
      "minLength": 1
    },
    "assessment_profile": {
      "enum": [
        "opportunity",
        "intelligence"
      ]
    },
    "components": {
      "type": "object"
    },
    "warnings": {
      "type": "array",
      "items": {
        "type": "string"
      }
    }
  },
  "$defs": {
    "assessment_component": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "status",
        "score",
        "reason",
        "evidence"
      ],
      "properties": {
        "status": {
          "enum": [
            "available",
            "unavailable"
          ]
        },
        "score": {
          "oneOf": [
            {
              "enum": [
                0.0,
                0.25,
                0.5,
                0.75,
                1.0
              ]
            },
            {
              "type": "null"
            }
          ]
        },
        "reason": {
          "type": "string"
        },
        "evidence": {
          "type": "array",
          "items": {
            "type": "string"
          }
        }
      }
    }
  }
}
```

Profile-specific validation must additionally enforce:

Opportunity requires exactly:

- `user_policy_fit`
- `opportunity_feasibility`

Intelligence requires exactly:

- `career_relevance_strength`
- `signal_significance`

============================================================
17. CANONICAL OPPORTUNITY OUTPUT
============================================================

```json
{
  "schema_version": "priority_assessment_v1",
  "signal_id": "signal_xxx",
  "assessment_profile": "opportunity",
  "components": {
    "user_policy_fit": {
      "status": "available",
      "score": 0.75,
      "reason": "The role substantially aligns with the user's stated career preferences.",
      "evidence": [
        "Specific supplied evidence."
      ]
    },
    "opportunity_feasibility": {
      "status": "unavailable",
      "score": null,
      "reason": "The supplied source does not provide sufficient qualification requirements.",
      "evidence": []
    }
  },
  "warnings": []
}
```

============================================================
18. CANONICAL INTELLIGENCE OUTPUT
============================================================

```json
{
  "schema_version": "priority_assessment_v1",
  "signal_id": "signal_xxx",
  "assessment_profile": "intelligence",
  "components": {
    "career_relevance_strength": {
      "status": "available",
      "score": 1.0,
      "reason": "The development directly affects a current target career direction.",
      "evidence": [
        "Specific supplied evidence."
      ]
    },
    "signal_significance": {
      "status": "available",
      "score": 0.75,
      "reason": "The source describes a concrete and meaningful organizational development.",
      "evidence": [
        "Specific supplied evidence."
      ]
    }
  },
  "warnings": []
}
```

============================================================
19. DETERMINISTIC PATH ALIGNMENT
============================================================

Path Alignment must NOT trigger another LLM judgment.

Inputs:

- matched career path IDs;
- TargetCareerPath fit_score;
- TargetCareerPath path_type.

Normalize the existing fit_score into 0-1 according to its actual live model
scale.

Recommended V1 path-type modifiers:

| Path type | Modifier |
|---|---:|
| `core` / `core_match` | 1.00 |
| `bridge_role` | 0.90 |
| `stretch_opportunity` | 0.75 |
| `exploratory_opportunity` | 0.65 |

For each valid matched path:

```
path_alignment =
normalized_fit_score * path_type_modifier
```

Use the strongest valid matched path as the V1 component score.

Preserve all resolved matched path IDs for explainability.

If no matched path can be resolved:

Path Alignment = unavailable.

============================================================
20. DETERMINISTIC RECENCY
============================================================

Preferred timestamp:

`CareerSignal.published_at`

Use explicit runtime `as_of`.

Recommended V1 normalized recency:

| Age | Score |
|---|---:|
| <= 3 days | 1.00 |
| 4-7 days | 0.90 |
| 8-14 days | 0.75 |
| 15-30 days | 0.55 |
| 31-60 days | 0.35 |
| > 60 days | 0.20 |

Missing/unparseable timestamp:

unavailable

Missing publication time must not be treated as stale.

============================================================
21. DETERMINISTIC SOURCE PROVENANCE
============================================================

Source type alone must NOT determine source quality.

Examples of prohibited assumptions:

RSS = high quality

Search API = low quality

Selected Website = high quality

Source Provenance is scored only when reliable structured provenance-quality
evidence exists.

Possible supported evidence may come from existing Source Monitoring evaluation
artifacts or explicitly supplied provenance context.

If no defensible structured quality evidence exists:

Source Provenance = unavailable.

No new source-quality ontology is introduced in Closing Stage 2.

============================================================
22. DETERMINISTIC AI CONFIDENCE
============================================================

Preferred input:

`FilterDecision.confidence`

Expected range:

0-1

Fallback:

`CareerSignal.relevance_score / 100`

only while the existing contract remains:

```
CareerSignal.relevance_score = AI Filter confidence * 100.
```

AI Confidence is a small supporting component.

It must not dominate priority.

============================================================
23. MISSING-DATA RENORMALIZATION
============================================================

Unavailable components are removed from the active denominator.

They are NOT assigned a score of zero.

For configured weights Wi and available normalized component scores Si:

```
Priority Score =
100 * sum(Wi * Si for available components) / sum(Wi for available components)
```

Example:

Opportunity configured weights:

| Component | Weight |
|---|---:|
| Path Alignment | 30 |
| User Policy Fit | 25 |
| Opportunity Feasibility | 20 |
| Recency | 15 |
| Source Provenance | 5 |
| AI Confidence | 5 |

Suppose Source Provenance is unavailable.

Available denominator:

95

The final score is computed only from the other five available components and
renormalized back to 0-100.

Therefore:

missing evidence != bad evidence.

============================================================
24. PRIORITY SCORE TIERS
============================================================

Final deterministic score range:

0-100

Tiers:

| Score range | Tier |
|---|---|
| 85-100 | `high` |
| 70-<85 | `medium_high` |
| 50-<70 | `medium` |
| 0-<50 | `low` |

============================================================
25. PRIORITY SCORE RESULT CONTRACT
============================================================

The deterministic scoring layer should produce a runtime result conceptually
equivalent to:

```
PriorityScoreResult(
    signal_id,
    priority_score,
    tier,
    profile,
    components,
    matched_path_ids,
    policy_version,
    warnings
)
```

Recommended policy version:

`career_signal_priority_v1`

The result must expose:

- final priority score;
- profile;
- tier;
- every configured component;
- whether each component was available;
- normalized component value;
- configured weight;
- weighted contribution;
- renormalization denominator;
- matched path IDs;
- policy version;
- warnings.

No separate post-aggregation cap or override is part of V1.

============================================================
26. DETERMINISM CONTRACT
============================================================

Given identical:

- CareerSignal;
- PriorityAssessmentResult;
- FilterDecision;
- TargetCareerPaths;
- UserPreferences;
- provenance context;
- `as_of`;
- scoring policy version;

the deterministic scoring layer must produce the same result.

The scoring layer must not contain:

- LLM calls;
- network calls;
- randomness;
- hidden dynamic weights.

============================================================
27. V1 PERSISTENCE BOUNDARY
============================================================

Closing Stage 2 does NOT add permanent Priority Score persistence.

Do not add:

- `career_signals.priority_score`;
- scoring database table;
- score repository;
- scoring migration.

Reason:

Priority is contextual to:

```
CareerSignal
+
current planning context
+
scoring policy
+
time.
```

It is not treated as a permanent global CareerSignal attribute.

Persistence can be reconsidered later if historical score auditability becomes
a demonstrated product need.

============================================================
28. EXPLICIT NON-GOALS
============================================================

This contract does NOT authorize:

- CareerSignal routing/grouping;
- cross-signal LLM Interpretation;
- Final Brief generation;
- new semantic job deduplication;
- new NLP extraction pipeline;
- score database persistence;
- Prompt Transparency infrastructure;
- source-monitoring redesign;
- scheduler or recurring execution.

============================================================
29. CLOSING STAGE 2 ACCEPTANCE PRINCIPLE
============================================================

A successful V1 implementation should allow a human reviewer to answer both:

"What semantic judgments did the AI make about this signal?"

and:

"How did the deterministic scoring policy turn those judgments and structured
evidence into this final Priority Score?"

without requiring another LLM explanation.
