# Steelman v2: composition under frozen view coverage

Status: implemented (harness `eval/run_v2.py`) and run in full on 2026-10-29; results in `docs/steelman-v2-results.md`. The thesis, falsifiers, and expectations below are unchanged from before the run. Supersedes nothing; v1 (the current eval) stays runnable. Deviations and choices made during implementation are recorded in "Implementation notes" at the end.

## Why v1 is not a steelman test

v1 smoke runs (one run per cell, 18 calls per arm) scored `documented_sql` 18/18 and `semantic_layer` 17/18. The documented arm did not slip on composition (Q2), joins (Q3), or the second grain (Q4) as the README predicted. Three reasons, all design flaws rather than findings:

1. **The views were written knowing the questions.** `vw_active_users` is Q1. `vw_qualifying_events` plus a date range is Q3 and Q4. Every question is one `COUNT(DISTINCT)` over a view that already encodes the whole definition. A view is governed logic stored in the warehouse: a lightweight semantic layer. v1 therefore compares a semantic layer to a semantic layer plus prose, and the prose never has to carry anything.
2. **Prose and governed objects are not separated.** The thesis is about documentation. The `documented_sql` arm bundles comments, a dictionary, and views, so a pass cannot be attributed to any of them.
3. **Every question has an exact-fit artifact on both sides.** Nothing measures the property the README actually claims for the layer: that defined primitives compose (measure x dimension x grain x filter) without a new artifact per question.

v2 keeps v1's mechanics (fingerprinted answers, four arms minus CSV, two languages, model gradient) and changes what is held fixed and what is asked.

## Thesis, restated so it can fail

> Given equal authoring effort per metric (one dictionary row in the warehouse, one measure in the layer), an agent answering compositions of governed metrics is more accurate, more consistent, and cheaper through the layer than through the documented warehouse, and the gap widens as compositions move away from the artifacts that happen to exist.

Falsifiers, pre-registered:

- F1. `documented_views` matches `semantic_layer` on accuracy and consistency across categories B-E (below) at the same agentic budget. Then the thesis reduces to "build governed objects" and the layer's remaining claims are footprint and loud failure.
- F2. `documented_prose` alone matches `semantic_layer` on B-E. Then documentation does substitute, and the README is wrong.
- F3. `semantic_layer` fails category C (ratios not defined as measures) in single-shot mode and the agentic mode does not recover it. Then the layer composes less than claimed and the honest statement is "the layer composes measures over dimensions and time; ratios must be defined."

Any of these is a publishable result. The spec is designed so that whichever holds is visible in the report, not argued around.

## Design rules

**R1. Artifact symmetry.** Every metric in the dictionary has exactly one authored artifact per side: a dictionary row (prose, warehouse) and a measure (executable, Cube). Authoring effort is equal; executability is the only difference. This is the thesis stated as a rule.

**R2. Views are frozen at v1's two.** `vw_active_users` and `vw_qualifying_events` stay. No view is added for any new metric or question. Justification: v1's dictionary already has three of five metrics with no curated object (`active_share`, `churned_users`, `paying_users`); data teams document faster than they build. A fourth arm variant, `documented_views_full` (a view per dictionary metric), is the F1 probe and is optional; if it closes the gap, say so.

**R3. Questions are written after artifacts are frozen**, by someone who does not edit `db/schema.py` or `cube/model`. Record the freeze commit in this file when it happens.

Freeze: artifacts (`db/schema.py`, `cube/model`, `data/`) frozen at commit `19a6fe4`. Any later change to them before the first run must be recorded here. Not met as written: the questions were fixed in this spec before the artifacts were built, but the same author wrote both.

**R4. Perfect retrieval stays.** The documented arms still get the whole catalog in the prompt. Imperfect retrieval would bias toward the thesis.

**R5. Both sides get the same composition primitives.** The semantic arm's query shape grows to the real Cube API (measures, dimensions, filters, time dimensions with granularity). The SQL arms get SQL. Neither gets a hint about which primitives the question needs.

## Arms

| arm | prompt contents | execution | mode |
|---|---|---|---|
| `raw_sql` | bare schema, profiling notes, samples | DuckDB, raw env | single-shot, agentic |
| `documented_prose` | comments + dictionary, **no views** | DuckDB, modeled env without views | single-shot, agentic |
| `documented_views` | comments + dictionary + view DDL (v1's `documented_sql`) | DuckDB, modeled env | single-shot, agentic |
| `semantic_layer` | live `/meta`: measures, dimensions, time dimensions, segments | Cube | single-shot, agentic |

`csv_in_context` is dropped. Its point (arithmetic unreliability on top of ambiguity) is made in v1 and it cannot do joins.

`documented_prose` needs `schema.build_modeled(con, ..., views=False)`; the dictionary's `source_object` column is blank for every row in that variant so the prose does not reference objects that do not exist.

### Single-shot protocol

As v1: one prompt, one reply, harness executes. The semantic reply is one Cube query:

```json
{"measures": ["<1..2 measures>"],
 "dimensions": ["<0..2 dimensions>"],
 "filters": [{"member": "...", "operator": "equals", "values": ["..."]}],
 "timeDimensions": [{"dimension": "...", "granularity": "day|week|month", "dateRange": ["YYYY-MM-DD", "YYYY-MM-DD"]}]}
```

Harness validates against `/meta` before sending; unknown members are a loud error, not a retry.

### Agentic protocol

The strongest case for SQL: the agent can probe `DISTINCT` values, test a query, read an error, and retry. Tool loop, max 8 tool calls, then a forced answer.

| tool | arms | returns |
|---|---|---|
| `run_sql(sql)` | the three SQL arms | up to 20 rows + column names, or the DuckDB error string |
| `cube_load(query)` | semantic | rows, or the Cube error string |
| `answer(value)` | all | ends the episode; `value` is a number or `{group: number}` |

Tokens and cost are summed over every turn. The transcript is stored per episode. Agentic mode runs on `documented_views` and `semantic_layer` for all questions, and on `raw_sql` and `documented_prose` for a subset (A and B) to bound cost; extend if budget allows.

## Question bank

Eight questions in six categories. Each lists the governed definition, what each arm must decide unaided, and the fingerprints the generator must keep distinct. Decision counts are the static number of silent choices a correct answer requires; the report prints them next to accuracy so the "smaller decision surface" claim is a measured column, not prose.

Governed vocabulary (unchanged from v1 unless noted): valid user = not deleted, not test. Qualifying event = not `email_opened`, not `webhook_ping`. Paid plan = `pro` or `enterprise`. Week = ISO, Monday 00:00 to next Monday 00:00. Month = calendar month, half-open.

### A. Controls (artifact exists on both sides)

**A1. How many active users do we have?** v1 Q1, answer 157. Expect every documented arm to pass. Included so a v2 regression is visible.

**A2. How many monthly active users did we have in June 2026?** v1 Q3, answer 160. Same purpose.

### B. Metric x dimension slice (dimension lives on the other table)

**B1. How many monthly active users on the pro plan did we have in June 2026?**

Governed: distinct valid users with `plan = 'pro'` and at least one qualifying event in June 2026.

| arm | decisions |
|---|---|
| raw_sql | exclusions (2), event filter, distinct, month boundary, join, plan = pro vs paid (7) |
| documented_prose | same, but exclusions and event filter are stated (7, 3 of them read from prose) |
| documented_views | join `vw_qualifying_events` back to `users` for `plan` (the view has no plan column), plan = pro vs paid, month boundary, distinct (4) |
| semantic_layer | measure `monthly_active_users`, filter `users.plan equals pro`, date range (3) |

Fingerprints to separate: governed; paid (pro + enterprise); pro with passive events; pro without exclusions (join to raw users); pro by `status = 'active'` instead of activity; event rows.

Cube requirement: `product_events` declares `joins: users` (belongs_to on `user_id`) so `users.plan` is filterable from the events cube. This is a structural artifact (one join, declared once), not a per-question one. Note it in the report as such.

### C. Cross-table ratio with no defined measure (the layer's edge)

**C1. What share of paying users were monthly active in June 2026?**

Governed: 100 x (distinct valid paid-plan users with a qualifying June event) / (valid paid-plan users). Numerator and denominator are both governed primitives; the ratio is in no dictionary row and no measure.

| arm | decisions |
|---|---|
| raw_sql | everything in B1 for the numerator, plus denominator population, plus paid definition agreeing across both (10) |
| documented_prose | same count, three read from prose |
| documented_views | numerator via view + join, denominator from `users` with exclusions reconstructed from prose, consistent paid definition (6) |
| semantic_layer, single-shot | **cannot be expressed as one query**: two measures from two cubes with different filters, and Cube does not divide ad hoc. Expected outcome: loud error or wrong. This is F3. |
| semantic_layer, agentic | two `cube_load` calls (`monthly_active_users` filtered to paid; `paying_users`) and arithmetic in the agent (2 queries, 1 division) |

Fingerprints: twelve numerator/denominator combinations as in v1's share question (paid vs pro, with/without exclusions, with/without passive), asserted >= 0.06pp apart.

Do not add a `paying_active_share` measure. Adding it would be adding a view by another name and would void R3. If the result is "the layer needs ratios defined", that is the finding.

### D. Metric grouped by dimension (multi-row answer)

**D1. Break down June 2026 monthly active users by plan.**

Governed: `{free: a, pro: b, enterprise: c}`, each the B1 definition for that plan. Scoring: exact match on all three cells; a wrong or missing cell is a wrong answer.

| arm | decisions |
|---|---|
| documented_views | join for plan, group key, month boundary, distinct per group (4) |
| semantic_layer | measure, dimension `users.plan`, date range (3) |

Fingerprints: the vector under each interpretation (governed; with passive; without exclusions; rows not users). The generator asserts the vectors are pairwise distinct, not just their sums.

### E. Time grain and multi-period

**E1. What was monthly active users for each month of Q2 2026?**

Governed: `{"2026-04": a, "2026-05": b, "2026-06": c}`, each the MAU definition over its calendar month. Traps: distinct per month vs distinct over the quarter, April 1 start, June 30 end, `BETWEEN` on the last day.

| arm | decisions |
|---|---|
| documented_views | `date_trunc` grouping, quarter boundaries, distinct per group (3) |
| semantic_layer | measure, granularity `month`, date range Apr 1 - Jun 30 (3, but granularity makes the per-month distinct and boundaries non-decisions) |

**E2. How many weekly active users did we have in the week of June 22, 2026?** v1 Q4, answer 136, kept because the semantic arm lost it once (`gpt-oss-20b` asked Saturday to Friday). v2 change: the semantic prompt documents `granularity: "week"`, and Cube's `week` granularity starts Monday, so a model that passes any range inside the week with `granularity: week` gets the governed bucket. A range spanning two weeks returns two rows and the harness rejects it: a loud error where v1 produced a silent 140. Verify Monday start on the pinned Cube image; if it differs, declare a custom granularity on `occurred_at` (`granularities:` with `interval: 1 week`, `origin: 2026-06-22`).

Fingerprints: v1's fifteen plus the two-bucket case.

### F. Threshold metric defined in prose only (R1 applied to a new metric)

**F1. How many engaged users did we have in June 2026?**

New dictionary row: `engaged_users` = count of valid users with **at least 3** qualifying product events in the calendar month. No view (R2). In Cube: a `user_months` cube over `vw_qualifying_events` grouped by `(user_id, date_trunc('month', occurred_at))` with `event_count` as a dimension, and `engaged_users` as `count` with filter `event_count >= 3`. One row, one measure: R1.

| arm | decisions |
|---|---|
| documented_prose | exclusions, event filter, per-user-per-month grouping, `>= 3` vs `> 3`, month boundary, distinct (6) |
| documented_views | grouping, threshold, boundary (3) |
| semantic_layer | measure, date range (2) |

Fingerprints: governed; `> 3`; `>= 3` counting passive events; `>= 3` over all time; `>= 3` events including test accounts; event rows. Crafted separators: valid users with exactly 2 qualifying June events plus 1 passive (counted under "passive included"), exactly 3 qualifying (lost under `> 3`), and exactly 3 qualifying across May-June but 2 in June (lost under "per month").

### Decision surface summary

| question | raw | prose | views | semantic |
|---|---|---|---|---|
| A1 | 3 | 3 | 1 | 1 |
| A2 | 6 | 6 | 2 | 2 |
| B1 | 7 | 7 | 4 | 3 |
| C1 | 10 | 10 | 6 | n/a single-shot; 3 agentic |
| D1 | 7 | 7 | 4 | 3 |
| E1 | 7 | 7 | 3 | 3 |
| E2 | 8 | 8 | 3 | 2 |
| F1 | 8 | 8 | 3 | 2 |

Re-derived against the final prompts. One disagreement, resolved in favor of the table: the F1 section above lists `documented_prose` at 6 because it counts the two exclusions as one choice; every other row counts them separately, so F1 prose is 8, matching raw. C1 semantic single-shot is reported as n/a.

## Metrics

Per (question, language, arm, mode, model) cell, 5 runs:

1. **Accuracy**: governed answer (exact for counts and vectors, +-0.02pp for shares).
2. **Consistency**: distinct resolved answers per cell. The v1 smoke runs never measured this; v2 reports it as the second headline.
3. **Failure taxonomy**: `correct` / `silent_wrong` (resolved to a number, governed or not) / `loud_error` (query error, schema violation, row-count mismatch, invalid member). The claim "the layer turns silent errors loud" becomes a column.
4. **Footprint and cost**: input and output tokens per episode (summed over turns in agentic mode), $ per correct, wasted $. As v1.
5. **Decision count**: static, from the table above, printed beside accuracy per question and arm.
6. **Agentic turn count**: mean tool calls to answer, per arm. A SQL agent that needs five probes to match a one-query semantic answer is a cost result even if accuracy ties.

## Data and generator changes

- Keep v1 users and events byte-stable where possible; add crafted separators for B1 (users whose plan separates pro from paid under each reading), D1 (vector distinctness), and F1 (threshold separators). Each new separator is deterministic and documented in `generate_events` as v1's A-D groups are.
- `fingerprints.json` gains one block per question; vector-valued blocks store lists. `_assert_distinct` learns to compare tuples.
- `eval/arms.py` mirrors every block in DuckDB SQL, and a parity check (generator vs harness) runs before any API call, as v1 does implicitly via the ground-truth print.

## Schema and Cube changes

`db/schema.py`
- `build_modeled(..., views: bool = True)`.
- Dictionary row `engaged_users` (Growth Analytics, `source_object` blank).
- No new views (R2). No new comments beyond what the new row needs.

`cube/model`
- `product_events`: `joins: users` on `user_id`; expose `users.plan` via the join (no new dimension on `product_events`).
- New cube `user_months` (sql over `vw_qualifying_events`), measure `engaged_users`, with the same OFFICIAL METRIC description style.
- Verify `week` granularity origin; add a custom granularity only if needed.
- Nothing else. In particular no `paying_active_share`.

## Harness changes

- Arm registry keyed by name with `prompt`, `resolve`, `env` so `documented_prose` is a config entry, not a fourth `if` chain.
- Multi-row resolution: SQL results with two columns become `{str(key): float(value)}`; Cube rows likewise, with month buckets normalized to `YYYY-MM`.
- Agentic loop in `providers.py` (OpenRouter tool calling is OpenAI-shaped; `gpt-oss-20b` and the GPT-6 models support `tools`; verify Haiku 4.5 and Sonnet 4.6 via `/meta` `supported_parameters`, which v1 probing showed include `tools`).
- Report: per-question tables gain `mode`, `loud_error`, and `decisions` columns; a new section ranks arms by consistency; a new section lists the F1-F3 falsifiers with the measured result next to each.

## Volume and cost

First pass as configured in `eval/v2_config.py` (four models, three runs, both languages): 768 single-shot calls plus 528 agentic episodes. The agentic count is (8 x 2 + 3 x 2) arm cells, not the 8 x 2 + 2 x 2 originally written here: categories A and B contain three questions. Estimated from smoke-run cost per (model, mode, arm): about $7.50 at list rates, $5.20 of it Haiku and Sonnet agentic. Haiku agentic episodes average about $0.026, more than Sonnet's single-shot calls, because Haiku takes more turns.

Full run (six models, five runs): 1,920 single-shot calls plus 1,320 agentic episodes, 2.5x the first pass. Estimate about $23: the four first-pass models scaled to five runs ($12.50), plus Terra at 1.17x Haiku ($5.95) and Sol at 0.67x Sonnet ($4.75), ratios taken from v1's per-model costs. Terra and Sol are not measured in v2, and the agentic costs rest on one or two smoke episodes per arm, so treat $15-35 as the plausible range.

## Pre-registered expectations

Written before implementation so the report can be checked against them.

- A: all documented arms and the layer pass; `raw_sql` fails at v1 rates.
- B, D, E: `documented_views` loses accuracy on the join-back and grouping decisions, mostly on the weaker models and in Portuguese; `documented_prose` loses more; the layer holds. If this does not happen, F1 or F2.
- C: single-shot semantic fails loudly; agentic semantic recovers at 2 queries; `documented_views` agentic recovers at 3+ queries with more variance. If agentic semantic does not recover, F3.
- E2: the layer's errors become loud (two-bucket rejection) rather than silent (140).
- F: `documented_prose` splits on `>= 3` vs `> 3` and on per-month grouping; the layer is exact.
- Consistency: the layer has the fewest distinct answers per cell everywhere except C single-shot.
- Footprint: the layer's input per call is roughly half of `documented_views` in single-shot, and the gap widens in agentic mode because SQL probes re-ship the catalog each turn.

## Out of scope

- Definition-change propagation and access denial. Both are properties of the artifacts, not of model behavior, and v2 does not pretend to measure them.
- Stale or inconsistent documentation. A steelman gives documentation its best shot; drift experiments are a different test and would bias toward the thesis.
- Timezones, fiscal calendars, multi-hop joins. Listed in the README as what the toy does not show; still true.

## Open decisions for the author

1. Keep Sol and Terra for the full run, or hold the gradient at four models? Spec assumes four for the first pass.
2. Agentic mode on `raw_sql` and `documented_prose`: A/B subset only (spec), or all eight at roughly double the agentic budget?
3. F1 (`documented_views_full`): run it in the same pass or only if F1 looks live after the first pass?

## Implementation notes

Choices made while implementing, recorded so the run can be judged against them.

**Open decisions, as defaulted.** (1) First pass at four models, `N_RUNS = 3`. (2) Agentic `raw_sql` and `documented_prose` on categories A and B only. (3) `documented_views_full` not implemented; build it if F1 is live after the first pass.

**Files.** `eval/run_v2.py` (runner, scoring, report), `eval/v2_config.py`, `eval/v2_arms.py` (registry, prompts, Cube validation, result shaping), `eval/v2_agent.py` (tool loop), `eval/v2_fingerprints.py` (DuckDB fingerprints and the parity check). Shared with v1: `eval/providers.py` (gained `chat()` with tools), `eval/costing.py`, pricing and routing in `eval/config.py`, catalog rendering in `eval/arms.py` (gained `views=False`). `cube/model/cubes/user_months.yml` is new; `product_events.yml` gained the `users` join.

**Data.** v1 users and events are not byte-stable, but every v1 fingerprint is unchanged (checked against the previous `fingerprints.json`). Random data already separated most v2 readings, so only two separators were needed, both documented in `data/generate_data.py`:
- Group E (one event moved). A valid user with exactly three qualifying June events has the latest moved to June 30 afternoon, so the F1 BETWEEN bug (152) differs from governed (153). Earlier June events remain, so no MAU, WAU, or per-month count moves.
- Plan swap P (two users). Without it, B1's BETWEEN bug and the pro status-active count were both 54. Pro/enterprise swaps cannot separate them; a free/pro swap between two valid inactive or churned users does, and it leaves v1's plan-dependent counts (any paying plan, paying and active) unchanged.
The spec's listed F1 separators (2 qualifying + 1 passive, exactly 3, 3 across May and June) already exist in the random data; the generator asserts distinctness rather than crafting them.

**Fingerprints.** C1 has 14 readings, not 12: the twelve numerator/denominator combinations plus `pro_mau / valid_pro_users` and the inverted reading (paying share of MAU). Minimum gap 0.20pp. D1 and E1 vectors are asserted distinct as whole vectors; E1 also includes the scalar "distinct users over the whole quarter". E2's "two-bucket case" is not a fingerprint: it is a loud error (`Expected one row, got 2`), as the E2 section intends.

**Cube.** Week granularity starts Monday on the pinned image (bucket `2026-06-22`), so no custom granularity was declared. A Saturday-to-Friday range with `granularity: week` returns two buckets (loud); without granularity it still returns a silent 140.

**Scoring.**
- Outcomes are `correct`, `silent_wrong`, `loud_error`, and `api_error`. `api_error` (transport failures after retries) is reported but excluded from accuracy and consistency, since it is not model behavior.
- Consistency is the mean distinct resolved answers per cell over cells with at least one resolved answer. A cell of only loud errors has no consistency to measure; counting it as 0 would reward failing loudly.
- Scalar questions require exactly one result row; more is loud. Single-shot Cube answers with two measures are loud (`Cube does not combine measures`): a two-measure C1 query returns `paying_users` filtered by the event time range (79, not 134), so dividing would be silently wrong.
- Breakdown keys are normalized to the ground truth's key space (lowercase plans; `YYYY-MM` months from dates, month names in English or Portuguese, or month numbers). Wide one-row pivots are accepted.

**Agentic loop.**
- Reasoning is passed back between turns (OpenRouter `reasoning_details`). Without it `gpt-oss-20b` lost the thread after a tool result.
- A turn that ends without a tool call is followed by a forced `answer` turn, not a text nudge. `gpt-oss-20b` on its bf16 hosts often reasoned to the answer ("Thus answer 56") and then emitted no tool call; forcing recovered it (4 of 4 in the smoke test). If the forced turn also fails, the episode is a loud error.
- `cube_load` accepts the query fields at the top level as well as under `query`.
- OpenRouter 429s and the 402 `in_flight_budget_exhausted` refusal are retried up to five times, honoring `Retry-After`; a 402 for exhausted credits is not.

**Falsifier thresholds** (fixed in `eval/v2_config.py` before any full run): "matches" means accuracy within 5pp of the layer's or above AND mean distinct answers within 0.25 of the layer's or below; "recovers" means agentic accuracy on C at or above 80%. F1 is evaluated on agentic B-E, F2 on single-shot B-E with an agentic check on B, F3 on semantic C in both modes.

**Prompt footprint.** Listing dimensions grew the semantic prompt: about 1,070 input tokens single-shot vs about 1,520 for `documented_views`, a 0.7x ratio rather than v1's 0.5x.

## Post-run addition: spreadsheet in context (2026-10-30)

Added after the 2026-10-29 results, written and committed before this addition ran. It does not revise the thesis, falsifiers, or expectations above, and it is not part of F1-F3.

**Why.** Many people answer data questions by pasting a spreadsheet into a model's context. v1 tested that only on one question, with no documentation. That setup conflates two failures: not knowing the definition, and miscounting rows.

**Design.**
- Two single-shot arms, given only `users.csv`:
  - `csv_raw`: v1's spreadsheet prompt.
  - `csv_documented`: the same, plus the users table and column notes and the full metrics dictionary, rendered from the catalog the documented arms read.
- Both prompts ask for percentages with two decimal places, since shares are scored within 0.02pp and a correctly rounded "57.9" would otherwise count as wrong.
- They run on the two questions `users.csv` alone can answer:
  - A1 (active users, 157);
  - A3 (active share, 57.93%; v1's Q2, new to v2).
- A3 also runs on the four core arms, single-shot only. Its decision counts are raw 5, prose 5, views 3, semantic 1, both CSV arms 5.
- Questions needing `events.csv` (about 125-140k tokens) were left out on cost.

**Volume and cost.** 360 calls on A3 (six arms) plus 120 on A1 (two CSV arms): 480 calls. About $4.50 at list rates, dominated by the CSV prompts (about 11-13k input tokens each).

**Expectations, written before the run:**
- `csv_raw` fails on definitions the way `raw_sql` does (A1 single-shot: 77%). Counting errors on weaker models stack on top, so it scores at or below `raw_sql` on both questions.
- `csv_documented` gets the definitions right but loses accuracy to counting:
  - The models that reason before answering (GPT-6.1 Sol and Terra) score high.
  - Claude without extended thinking and the two small models score low.
  - Overall it lands clearly below `documented_prose`, which runs the count in a database.
- A3 is harder than A1 for both CSV arms: two counts and a division instead of one count.
- The semantic layer scores about 100% on both questions, at roughly a tenth of a CSV call's input.
- No language effect, as in the main run.

Result (2026-10-30): run and scored in `docs/steelman-v2-results.md`, section "Post-run addition". Two parser bugs found after the run were fixed and every episode re-scored from its saved reply: decimal commas, and numbers taken from visible working instead of the final answer. The as-run scores are kept alongside. The expectations above are unchanged.
