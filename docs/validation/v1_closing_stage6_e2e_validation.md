# AgentWorkflow V1 Closing Stage 6 E2E Validation

## Validation Scope

- Validation date: 2026-08-12
- Release-candidate branch: `feature/v1-closing-stage6-e2e-validation`
- Code commit under validation: `e438bed32cb6a4278869f82bb4b7f69a48775b0d`
- Stage 5 baseline tag: `v1-closing-stage5-complete`
- Preflight worktree: clean
- Validation resumed after the committed Priority Assessment contract-retry fix.
- The user explicitly authorized the required real user context to the configured DeepSeek endpoint and user-derived queries to the configured Brave Search endpoint.
- No private profile values, preferences, queries, prompts, or acquired content are reproduced in this record.

## Normal Entrypoints

- Search API validation invoked `src.main.main()`, the normal V1 `python main.py` application path.
- Monitoring validation invoked `MonitoringRuntime.run()` with the normal production repositories and live clients.
- Wrappers were limited to transparent call counters, bounded execution settings, and temporary path redirection; the application pipelines themselves were not replaced.

## Isolation And Safety

- All live validation used a fresh temporary SQLite database under an ignored workspace directory.
- The isolated database was initialized through the normal migration and repository path.
- The TargetCareerPath cache was redirected to the same temporary area.
- Generated reports and wrapper output were redirected to the temporary area.
- The production database and production TargetCareerPath cache were not used for validation writes.
- The temporary validation directory was removed after evidence was recorded.

Production database baseline and final state:

- Path: `data/agentworkflow.db`
- Size: `290816` bytes
- UTC modification time: `2026-08-02T02:52:02.8453504Z`
- SHA-256: `AEBDA580F57B604DFF79491D81077889E2C0115B77F415C10D3281B558461AA6`
- Final comparison: unchanged

Production TargetCareerPath cache baseline and final state:

- Path: `outputs/planning/target_career_paths.json`
- Size: `39667` bytes
- SHA-256: `07558A8535BEED183C03CDD6F38D29C0C8D63D04654A3C7BB56866D8CDFD4F89`
- Final comparison: unchanged

## Runtime Configuration

- Search API dry run: disabled
- AI Filter dry run: disabled
- Priority Assessment: enabled
- Career Intelligence Interpretation: enabled by the normal runtime policy
- Search plan execution limit: one plan per pass
- AI Filter item limit for Search API: five new candidates on the first pass
- Monitoring acquisition caps: three candidates initially, with one RSS expansion to five
- TargetCareerPath generation: real configured DeepSeek client, isolated cache
- Search acquisition: real configured Brave Search client
- Monitoring acquisition: real approved RSS and Selected Website handoffs
- All runs were deliberately bounded.

## Planning Bundle And Career Paths

- The first Search API run generated and persisted one planning bundle.
- Planning bundle ID: `1`
- Planning fingerprint: `68765aef53a6297388ae83f3a34e62f7bf69512b5bfd8cb4b998e3171264773f`
- Planning output hash: `f820f1a8fd40903842429bfe9d45c125e982ac70e2a19cec97fe36015d91a2c0`
- Generation mode: `generated`
- Provider/model: configured DeepSeek / `deepseek-v4-pro`
- Prompt version: `target_career_path_prompt_v1`
- Result: 9 TargetCareerPaths, 72 queries, and 72 SearchPlans.
- The second Search API run reused planning bundle ID `1` without another TargetCareerPath model call.
- Monitoring hydrated the same persisted TargetCareerPaths from the planning bundle.
- The generated planning bundle was derived from the current authorized context; all later runs used that exact persisted context without regeneration or private-value substitution.

## Search API E2E

First pass:

- Pipeline run: `run_72d9673906664964ac0dbc4b27293955`
- Pipeline lifecycle status: `completed`
- Search plans executed/skipped: 1 / 71
- Brave results returned: 10
- New external SourceItems: 10
- AI Filter calls: 5
- Filter outcomes: 3 accepted, 2 rejected, 5 deferred by the configured item limit
- CareerSignals created: 3
- Priority Assessment invocations: 3
- Priority Assessment model attempts: 3
- Priority corrective retries: 0
- Career Intelligence Interpretation calls: 0 because no Intelligence signals were accepted
- Final Brief: valid `career_intelligence_brief_v1`
- Final Brief contents: 3 Opportunities, 0 Key Developments, 0 Themes, 0 Career Implications, 1 contract-valid warning

Immediate duplicate pass:

- Pipeline run: `run_dcbe0fef731a4d96aa53680dd0de1d33`
- Pipeline lifecycle status: `completed`
- Brave results returned: the same 10 canonical items
- New SourceItems: 0
- Existing SourceItems updated: 10
- AI Filter calls: 0
- Priority Assessment calls: 0
- Career Intelligence Interpretation calls: 0
- TargetCareerPath calls: 0
- Final Brief: valid empty `career_intelligence_brief_v1`
- Every Search API SourceItem had `seen_count = 2` after the duplicate pass.

The non-empty Brief preserved signal identity, canonical title, organization, URL, nullable publication time, summary, deterministic priority score/tier, matched path identity, and available component reasons. Ordering matched deterministic priority order. Missing publication times and unavailable component reasons remained contract-valid null values and were not fabricated.

## Source Monitoring E2E

The validation used the approved persisted acquisition handoffs without source discovery:

- One RSS newsroom handoff
- One Selected Website reports/data handoff
- One Selected Website official-homepage handoff

Five Monitoring pipeline runs completed:

- RSS initial pass: 3 new candidates, 3 AI Filter calls, 3 rejected
- RSS immediate duplicate pass: 3 historical candidates, 0 semantic calls, 3 deferred
- Selected reports/data pass: 3 new candidates, 3 AI Filter calls, 3 rejected
- RSS expansion pass: 5 candidates, 3 historical and 2 new, 2 AI Filter calls, 2 rejected
- Selected official-homepage pass: 3 new candidates, 3 AI Filter calls, 3 rejected

All five runs produced a valid empty `career_intelligence_brief_v1` and completed normally. The Stage 4 interpretation object was also valid and empty in each run, and its sections were copied exactly into the Brief. Real source content produced no accepted Monitoring CareerSignal in this bounded sample, so Priority Assessment and multi-signal Interpretation were not naturally invoked for Monitoring. This is recorded as a validation concern rather than a contract failure: acquisition, identity, history, filtering, empty interpretation, Brief assembly, and lifecycle behavior were exercised; no acceptance was fabricated.

## Persistence And Lifecycle

Final isolated database totals:

| Record | Count |
| --- | ---: |
| Pipeline runs | 7 |
| Planning bundles | 1 |
| Source executions | 7 |
| Source items | 21 |
| Source item discoveries | 37 |
| Filter executions | 16 |
| Filter decisions | 16 |
| CareerSignals | 3 |

Additional persistence checks:

- All 7 PipelineRuns ended in `completed`; none remained running or failed.
- All 7 SourceExecutions ended in `complete`.
- SourceItems were canonicalized and reused across repeated acquisition.
- SourceItemDiscovery retained per-execution observations: 20 Search API, 11 RSS, and 6 Selected Website discoveries.
- The isolated database contained 10 Search API, 5 RSS, and 6 Selected Website SourceItems.
- The three persisted CareerSignals came from Search API and were categorized as job Opportunities.
- Priority scores, routing, interpretation, and final Briefs remained runtime-only as designed; no new persistence tables or DB behavior appeared.

## Historical Memory

- The immediate Search API duplicate pass rediscovered 10 historical items and made zero repeated AI Filter, Priority Assessment, or Interpretation calls.
- The immediate RSS duplicate pass rediscovered 3 historical items and made zero repeated AI Filter, Priority Assessment, or Interpretation calls.
- The expanded RSS pass reused 3 historical items while filtering only its 2 new items.
- Repeated acquisition increased `seen_count` and added SourceItemDiscovery rows without creating duplicate canonical SourceItems.
- Historical-memory behavior passed for both Search API and Monitoring paths.

## Convergence And Economics

- Search API and Monitoring acquisition both converged on canonical SourceItem, SourceItemDiscovery, FilterDecision, CareerSignal, routing, interpretation, and Brief contracts.
- Downstream scoring does not infer semantic quality from `source_type` or domain.
- Search API produced the live Opportunity branch; Monitoring produced valid empty downstream collections because its bounded real candidates were rejected.
- Final Brief assembly made zero model calls and was deterministic.

Live model-call accounting:

| Operation | Calls/attempts |
| --- | ---: |
| TargetCareerPath generation | 1 |
| AI Filter | 16 |
| Priority Assessment | 3 |
| Career Intelligence Interpretation | 0 |
| Career Intelligence Brief assembly | 0 |
| Total model calls/attempts | 20 |

External acquisition requests:

- Brave Search requests: 2, including the duplicate-memory pass
- RSS requests: 3
- Selected Website requests: 2

## Product Sanity Review

- Brief structure: PASS
- Opportunity readability and correctness: PASS
- Intelligence readability: NOT APPLICABLE for this bounded live sample
- Source/history observability: PASS
- Overall product sanity: PASS

The live Opportunity Brief was understandable and retained the expected URLs, organizations, summaries, priorities, and path references. Null dates and unavailable reasons reflected absent source evidence. Empty Intelligence sections and one warning were transparent and contract-valid.

## Tests

Command:

```text
python -B -m unittest discover -s tests
```

Result:

- 1627 tests run
- 0 failures
- 0 errors
- Duration: 51.336 seconds
- Overall: PASS

## Accepted V1 Limitations

- Real source variance can yield no accepted Intelligence signals in a bounded Monitoring sample.
- Empty Intelligence sections are valid and are surfaced with a warning rather than synthetic content.
- Publication time and some assessment reasons can remain null when evidence is unavailable.
- Live acquisition counts and accepted content can vary between future executions.

## Final Verdict

- Search API E2E: PASS
- Source Monitoring E2E: CONCERN, with valid bounded empty-result behavior and no contract failure
- Final Career Intelligence Brief: PASS
- Historical duplicate behavior: PASS
- Planning-bundle reuse and DB lifecycle: PASS
- Production-state isolation: PASS
- Full automated suite: PASS
- Blocking issues: none
- Overall Closing Stage 6 verdict: PASS
