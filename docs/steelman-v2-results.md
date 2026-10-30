# Steelman v2 results

Full run of the steelman v2 eval (spec: `docs/steelman-v2-spec.md`), 2026-10-29. Raw outputs are in `results/v2/`, which is gitignored: `results.csv` has one row per episode, `report.md` is the generated report, and `transcripts/` holds every prompt, reply, and tool call.

## Verdict

**The pre-registered thesis did not survive.** Two of the three falsifiers triggered:

- F1: the documented warehouse with views matched the semantic layer at the same agentic budget.
- F2: the warehouse with prose alone matched the layer in single-shot mode.

Both still hold after removing the harness artifact found in D1 and after excluding the weakest model. F3 did not trigger: the layer cannot answer an undefined ratio in one query, but agents recover it in two queries.

On this toy warehouse, documentation substitutes for the semantic layer on accuracy and consistency. The models read a seven-row metrics dictionary and reconstruct the exclusions, joins, distinct counts, and thresholds correctly, in English and Portuguese.

What the evidence still supports is narrower:

1. Documentation of any kind is decisive. Without it (`raw_sql`), accuracy is 11% single-shot and 22% agentic. Almost every failure is a confident wrong number.
2. The layer is cheaper per correct answer: 19% cheaper single-shot and 39% agentic, with 43% fewer input tokens per agentic episode.
3. The layer is immune to SQL dialect errors. In single-shot E1, documented-arm queries called PostgreSQL's `to_char` against DuckDB 26 times (18 by Sonnet, 8 by gpt-oss-20b). That accounts for all of the documented arms' single-shot E1 losses.
4. The layer does not make boundary bugs impossible. Haiku reproduced the BETWEEN bug inside Cube by filtering with `lte "2026-06-30"` instead of a `dateRange`.

## Run

| | |
|---|---|
| Commit | `bcac3b2`. Artifacts frozen at `19a6fe4`, per rule R3 |
| Models | Claude Sonnet 4.6, GPT-6.1 Sol, Claude Haiku 4.5, GPT-6.1 Terra, GPT-6 Luna, gpt-oss-20b (bf16 hosts) |
| Cells | 8 questions x 2 languages x 6 models, 5 runs each |
| Episodes | 1,920 single-shot + 1,320 agentic = 3,240 |
| Outcomes | 2,496 correct, 601 silent wrong, 143 loud error, 0 api errors |
| Spend | $14.35 at list rates (71% agentic; the estimate was about $23) |
| Wall time | 39 minutes, 8 workers |
| Fingerprint parity | harness SQL matched the Python generator on every block before any call |

## Falsifiers

Thresholds were fixed in `eval/v2_config.py` before the run. "Matches" means accuracy within 5pp of the layer's or above, AND mean distinct answers per cell within 0.25 of the layer's or below. "Recovers" means agentic accuracy on C at or above 80%.

| falsifier | comparison | documented arm (accuracy, distinct) | semantic layer (accuracy, distinct) | verdict |
|---|---|---|---|---|
| F1: views match the layer | agentic, B-E | 96%, 1.22 | 96%, 1.22 | **triggered** |
| F2: prose alone matches the layer | single-shot, B-E | 92%, 1.12 | 73%, 1.04 | **triggered** |
| F2, agentic check | agentic, B | 100%, 1.00 | 98%, 1.08 | **triggered** |
| F3: layer cannot compose an undefined ratio | semantic on C | single-shot 0% | agentic 85% | not triggered |

Sensitivity checks (post hoc, not pre-registered):

| check | documented arm | semantic layer | still triggered? |
|---|---|---|---|
| F1 without gpt-oss-20b | views 100%, 1.06 | 99.2%, 1.06 | yes |
| F2 without C (impossible single-shot by design) and with the D1 two-key artifact re-scored | prose 91%, 1.24 | 96%, 1.04 | yes, at the boundary: the accuracy gap is exactly 5pp and the distinct gap is 0.20 |

The F2 headline (92% vs 73%) overstates the documented arm, because the layer's 73% includes C1, which it cannot express in one query, and the D1 artifact. Corrected, the layer leads by 5pp and is more consistent. That is still inside the pre-registered "match" band, so F2 stands, but only just.

## Accuracy by question

Single-shot (each cell is 60 episodes: 6 models x 2 languages x 5 runs):

| question | raw_sql | documented_prose | documented_views | semantic_layer |
|---|---|---|---|---|
| A1 active users | 77% | 100% | 100% | 100% |
| A2 June MAU | 0% | 100% | 100% | 100% |
| B1 pro-plan MAU | 0% | 100% | 100% | 100% |
| C1 paying active share | 12% | 95% | 83% | 0% (59 of 60 loud, by design) |
| D1 MAU by plan | 0% | 98% | 100% | 72% (92% re-scored) |
| E1 MAU per month of Q2 | 0% | 78% | 78% | 100% |
| E2 WAU, week of June 22 | 2% | 88% | 92% | 93% |
| F1 engaged users | 0% | 75% | 100% | 100% |
| **all** | **11%** | **92%** | **94%** | **83%** |

Agentic (up to 8 tool calls; `raw_sql` and `documented_prose` run on A and B only):

| question | raw_sql | documented_prose | documented_views | semantic_layer |
|---|---|---|---|---|
| A1 active users | 58% | 100% | 100% | 100% |
| A2 June MAU | 3% | 97% | 97% | 100% |
| B1 pro-plan MAU | 5% | 100% | 100% | 98% |
| C1 paying active share | | | 95% | 85% |
| D1 MAU by plan | | | 100% | 98% |
| E1 MAU per month of Q2 | | | 98% | 100% |
| E2 WAU, week of June 22 | | | 87% | 97% |
| F1 engaged users | | | 98% | 100% |
| **all** | **22%** | **99%** | **97%** | **97%** |

Agentic mode lowered `raw_sql` accuracy on A1 (77% to 58%). Exploring the bare tables led models to confident wrong definitions more often than not exploring.

## What went wrong without documentation

`raw_sql` failed 566 times. 537 of those were silent: a number came back and nothing flagged it. The fingerprints show which definition each model chose:

| question | most frequent wrong readings |
|---|---|
| A1 | 26x `status = 'active'` only (174); 12x active excluding deleted only (164) |
| A2 | 62x passive events counted (162); 32x everyone and every event (184) |
| B1 | 88x passive events counted (57); 13x status also required to be active |
| C1 | 42x paid MAU with passive events / valid paid users |
| D1 | 44x per-plan MAU with passive events |
| E1 | 31x unrecognized; 12x passive events counted |
| E2 | 24x everyone and every event (165); 23x passive events counted (144) |
| F1 | 33x unrecognized; 15x answered the June MAU (160) instead |

The dominant trap is behavioral: counting `email_opened` and `webhook_ping` as activity. The schema alone gives no reason to exclude them. One sentence in a column comment fixes it, which is why every documented arm clears it.

## Where the documented arms failed

**SQL dialect.** All 13 E1 failures in `documented_prose` and all 13 in `documented_views` (single-shot) are loud `to_char` errors. `to_char` is a PostgreSQL function that DuckDB lacks. Sonnet produced 18 of the 26, and `gpt-oss-20b` the rest. These are SQL-synthesis failures, not definition failures, and agentic mode fixed them (views E1 rose to 98%): the agent reads the error and rewrites the query.

**Malformed SQL.** In F1 `documented_prose`, Sonnet and Haiku emitted invalid SQL: a duplicated `HAVING COUNT(*) >= 3` clause, unbalanced subqueries, mismatched UNION columns.

**Definition slips.**
- F1 prose: Haiku answered the June MAU (160) instead of engaged users five times. Once the dictionary row has no view behind it, the prose alone did not stop it.
- C1: the most common unlabeled wrong answer is 119.40% = 160 / 134, all June MAU divided by valid paying users, with the numerator never filtered to paid plans. It appears 6 times in `documented_views` single-shot and 4 times in semantic agentic. This reading is missing from the C1 fingerprint set and should be added.
- E2: `gpt-oss-20b` repeatedly chose a Saturday-to-Friday week (140), the same miss it made through Cube in v1.

## Where the semantic layer failed

**C1, single-shot (by design).** 52 replies sent two measures, which the harness rejects loudly, because Cube applies the event-time filter to `paying_users` (79, not 134). Five replies declined in prose to write a query, explaining that no measure covers the ratio. Two hit Cube's own error, "Can't find join path". This is the expected F3 behavior: the failure is loud, which is the right way to fail.

**C1, agentic.** 51 of 60 recovered with about 2.8 `cube_load` calls (two counts, then the division). Of the 9 silent misses, 8 came from `gpt-oss-20b`, including the unfiltered-numerator 119.40%.

**D1, single-shot: a harness artifact.** 12 of the 17 failures are queries that added `granularity: "month"` alongside `users.plan` over a June-only range. Cube returned one row per plan in a single month bucket, which is the governed answer in all 12 cases. The harness rejected any breakdown grouped by two keys. Re-scored, D1 semantic single-shot is 92%. The fix is to collapse grouping keys that take a single value.

**The BETWEEN bug lives in the layer too.** The other 5 D1 misses are Haiku queries that filtered `product_events.occurred_at` with `gte "2026-06-01"` and `lte "2026-06-30"` instead of a `dateRange`. Cube compared against June 30 at 00:00 and dropped June 30 afternoon, giving `{free: 80, pro: 55, enterprise: 23}`, exactly the SQL BETWEEN fingerprint. The README's claim that the boundary bug "cannot happen there by construction" holds only for `dateRange`. Cube does not stop a time dimension from being filtered with comparison operators.

**E2.** Four single-shot misses, all `gpt-oss-20b`. Two were Saturday-to-Friday ranges with `granularity: week`, which returned two buckets and were rejected loudly. Two were silent wrong ranges (140 and 138) without granularity. Granularity turned half of the calendar errors loud, as the spec intended. It cannot catch a model that omits it.

## Model gradient

Single-shot accuracy on B1, D1, E1, E2, F1 (50 episodes per cell; D1 semantic as scored, not re-scored):

| model | raw_sql | documented_prose | documented_views | semantic_layer |
|---|---|---|---|---|
| Claude Sonnet 4.6 | 0% | 68% | 84% | 100% |
| GPT-6.1 Sol | 0% | 100% | 100% | 100% |
| Claude Haiku 4.5 | 0% | 84% | 100% | 90% |
| GPT-6.1 Terra | 2% | 100% | 100% | 100% |
| GPT-6 Luna | 0% | 100% | 100% | 90% |
| gpt-oss-20b | 0% | 76% | 80% | 78% |

Agentic, B-F including C:

| model | documented_views | semantic_layer |
|---|---|---|
| Claude Sonnet 4.6 | 100% | 100% |
| GPT-6.1 Sol | 100% | 100% |
| Claude Haiku 4.5 | 100% | 97% |
| GPT-6.1 Terra | 100% | 100% |
| GPT-6 Luna | 100% | 100% |
| gpt-oss-20b | 78% | 82% |

The prediction that the layer's advantage widens as capability drops did not hold:
- The weakest model fails at the same rate on every arm with guidance, about 80%. `gpt-oss-20b` accounts for 79 of the 118 failures on the guided arms (excluding the by-design C1 single-shot failures).
- The layer's clearest single-shot win is on Sonnet, the strongest Claude model, and comes from dialect errors, not from definitions.
- GPT-6.1 Sol, GPT-6.1 Terra, and GPT-6 Luna were perfect on both documented arms across these five questions.

## Language

Portuguese questions made no measurable difference:

| | raw_sql | documented_prose | documented_views | semantic_layer |
|---|---|---|---|---|
| single-shot en | 11% | 91% | 95% | 84% |
| single-shot pt | 11% | 92% | 94% | 82% |
| agentic en | 23% | 99% | 98% | 98% |
| agentic pt | 21% | 99% | 96% | 97% |

The expected "language boundary adds interpretation variance" effect is absent for these models.

## Consistency

Mean distinct resolved answers per (question, language, model) cell; cells with no resolved answer are excluded:

| mode | semantic_layer | documented_prose | documented_views | raw_sql |
|---|---|---|---|---|
| single-shot | **1.02** | 1.10 | 1.13 | 1.70 |
| agentic | 1.14 | **1.06** | 1.17 | 1.78 |

The layer is the most consistent arm in single-shot mode, by a small margin. In agentic mode the prose arm is more consistent than the layer, partly because the layer's agentic cells include C1, where models compose the ratio themselves.

## Loud and silent failures

Excluding the by-design C1 semantic single-shot failures:

| mode | arm | failures | loud | silent | loud share |
|---|---|---|---|---|---|
| single-shot | raw_sql | 426 | 28 | 398 | 7% |
| single-shot | documented_prose | 39 | 27 | 12 | 69% |
| single-shot | documented_views | 28 | 14 | 14 | 50% |
| single-shot | semantic_layer | 21 | 14 | 7 | 67% |
| agentic | all arms | 170 | 1 | 169 | 1% |

In single-shot mode, failures on the documented arms are as often loud as the layer's: a broken SQL query is loud too. In agentic mode nearly every remaining failure is silent on every arm: agents retry past errors, so what survives is a confident wrong number. The "layer turns silent errors loud" claim holds only for one-shot integrations, and there it does not separate the layer from documented SQL.

## Footprint and cost

| mode | arm | input tok / episode | output tok / episode | tool calls | total | $ / correct |
|---|---|---|---|---|---|---|
| single-shot | raw_sql | 658 | 286 | | $0.88 | $0.0162 |
| single-shot | documented_prose | 1,298 | 259 | | $1.18 | $0.0027 |
| single-shot | documented_views | 1,602 | 201 | | $1.21 | $0.0027 |
| single-shot | semantic_layer | 1,127 | 175 | | $0.88 | $0.0022 |
| agentic | raw_sql | 4,979 | 646 | 2.5 | $1.41 | $0.0353 |
| agentic | documented_prose | 5,839 | 452 | 1.7 | $1.52 | $0.0085 |
| agentic | documented_views | 6,615 | 445 | 1.7 | $4.51 | $0.0097 |
| agentic | semantic_layer | 3,784 | 318 | 1.2 | $2.76 | $0.0059 |

- The layer is the cheapest arm per correct answer in both modes: 19% below `documented_views` single-shot and 39% below it agentically.
- Its input footprint is 0.70x the views arm single-shot (not the predicted 0.5x; listing dimensions grew the semantic prompt) and 0.57x agentically. The predicted widening of the gap in agentic mode held: SQL agents re-ship the catalog every turn.
- Haiku drives much of the agentic gap. It averaged 4.3 tool calls per `documented_views` episode against 1.4 through the layer. Its agentic episodes cost $3.33, more than Sol's entire run ($2.82).
- `raw_sql` is the most expensive per correct answer by far: $0.0162 single-shot, about 6x the documented arms.

Spend by model: Sonnet $5.85, Haiku $3.89, Sol $2.82, Terra $1.51, Luna $0.18, gpt-oss-20b $0.11.

## Pre-registered expectations, scored

| expectation | result |
|---|---|
| A: documented arms and the layer pass; raw_sql fails | **held**: 98-100% vs 31-38% |
| B, D, E: views lose accuracy on join-back and grouping, mostly on weaker models and in Portuguese; prose loses more; the layer holds | **failed**: B and D at 98-100% on both documented arms. E losses are dialect errors, not definitions. No language effect |
| C: single-shot semantic fails loudly; agentic semantic recovers at 2 queries; views agentic recovers at 3+ queries with more variance | **partly held**: loud single-shot (59/60) and recovery (85%) held, but views needed fewer calls (2.2 vs 2.8), was more accurate (95% vs 85%), and varied less (1.58 vs 1.75) |
| E2: the layer's errors become loud | **partly held**: 2 of 4 loud |
| F: prose splits on >= vs > and on per-month grouping; the layer is exact | **partly held**: the layer is exact (100%), and prose fell to 75%, but through MAU confusion and malformed SQL, not threshold splits |
| Consistency: the layer has the fewest distinct answers everywhere except C single-shot | **held single-shot, failed agentic** |
| Footprint: about half of views single-shot, widening in agentic mode | **partly held**: 0.70x single-shot, 0.57x agentic |

## Threats to validity

- **Harness artifacts.** D1's two-key rejection cost the layer 12 correct answers. C1 lacks a fingerprint for 119.40% (MAU / paying users), so those answers read as "unrecognized". Neither changes a falsifier verdict.
- **One author.** Rule R3 asked for questions written by someone who did not build the artifacts. Here the same author wrote both, before the run.
- **A small, legible catalog.** Seven dictionary rows and two tables fit easily in context and leave little to misread. The spec rules out the conditions most likely to favor the layer (stale docs, multi-hop joins, catalogs too large to send whole), so this result is a ceiling for documentation, not a typical case.
- **Vendor defaults.** The OpenAI models reason before answering and ignore temperature; Claude runs without extended thinking. Cross-vendor gaps mix model quality with these defaults.
- **Prices.** All costs are at list rates. Actual OpenRouter billing for OpenAI models differs through automatic prompt caching.

## What this means for the README

Claims the data contradicts or narrows:

- "Traditional database tables, even well modelled and well documented, cannot substitute a semantic layer." Not supported here. Well-documented tables matched the layer on accuracy in both modes and on consistency in agentic mode.
- "The boundary bug cannot happen there by construction." True for `dateRange` only; filter operators on time dimensions reproduce it.
- "The semantic arm is structurally language-invariant" while the SQL arms pay a language penalty. Every arm was language-invariant at this scale.

Claims the data supports:

- Documentation is the decisive variable. Undocumented tables fail 78-89% of the time, almost always silently.
- The layer is cheaper per correct answer, and its footprint advantage grows when agents iterate.
- The layer removes SQL dialect as a failure class.

## Possible next steps

1. Fix the two harness artifacts: collapse single-valued grouping keys in D1, and add the 119.40% C1 fingerprint. Re-score from the existing transcripts; no new API calls are needed.
2. Build harder tests outside this spec's scope:
   - a catalog large enough that it must be retrieved rather than sent whole;
   - a metric definition changed in one place but not the other (drift);
   - multi-hop joins.

   These are where the layer's remaining structural claims (versioned definitions, propagation, enforcement) would be tested rather than assumed.
3. Revise the README thesis to match the evidence before presenting it.

## Post-run addition: spreadsheet in context (2026-10-30)

Many people answer data questions by pasting a spreadsheet into a model's context, so the addition compares that workflow with the other arms. It was designed and pre-registered after the results above (spec section "Post-run addition", commit `fca7945`), then run on the two questions `users.csv` alone can answer:
- A1, active users (157);
- A3, active share (57.93%; v1's Q2).

The two arms:
- `csv_raw`: the spreadsheet only.
- `csv_documented`: the spreadsheet plus the users column notes and the full metrics dictionary.

A3 also ran on the four core arms. Volume: 480 single-shot calls, $4.74 at list rates. Outputs: `results/v2_csv_a3/` and `results/v2_csv_a1/`; A1's core-arm numbers come from the main run.

### Result

A spreadsheet in context is the weakest way to ask. Documentation helps, but it does not fix the counting.

| question | raw_sql | documented_prose | documented_views | semantic_layer | csv_raw | csv_documented |
|---|---|---|---|---|---|---|
| A1 active users | 77% | 100% | 100% | 100% | 43% | 50% |
| A3 active share | 62% | 100% | 100% | 100% | 7% | 47% |

By model (A1 / A3):

| model | csv_raw | csv_documented |
|---|---|---|
| Claude Sonnet 4.6 | 30% / 10% | 0% / 20% |
| GPT-6.1 Sol | 100% / 0% | 100% / 100% |
| Claude Haiku 4.5 | 20% / 0% | 0% / 0% |
| GPT-6.1 Terra | 60% / 30% | 100% / 100% |
| GPT-6 Luna | 50% / 0% | 60% / 60% |
| gpt-oss-20b | 0% / 0% | 40% / 0% |

- **Two failure modes, separated.**
  - `csv_raw` fails on definitions first. On A3, 26 of 60 answers were 58.00%: status `active` divided by all 300 rows, ignoring the exclusions the data itself shows. The same reading appears in `raw_sql`.
  - With the definitions supplied, the misreadings disappear and what is left is miscounting: answers off by 1 to 32 users on A1. Sonnet came in 3 to 27 below 157 on every A1 run. Haiku ranged from 3 below to 32 above; one run returned 2, a row id.
- **Only the models that reason before answering count reliably.** GPT-6.1 Sol and Terra were perfect with documentation, on both questions and both languages. Claude, which runs without extended thinking, counted in visible text and was wrong 38 of 40 times with documentation. The small models land between.
- **Silent.** 89 of 90 `csv_raw` failures and 56 of 62 `csv_documented` failures are confident wrong numbers. The rest are empty replies from `gpt-oss-20b`.
- **Cost.** A spreadsheet call ships about 12,000 input tokens, against about 1,100 for the semantic layer. Per correct answer, `csv_documented` costs $0.038 and `csv_raw` $0.069, against $0.0014 for the semantic layer and $0.0019-0.0022 for documented SQL: 27x and 49x the layer.
- **Language.** `csv_raw` scored 30% in English and 20% in Portuguese; `csv_documented` 52% and 45%. That is a small gap, unlike the zero gap on every other arm, but at 60 episodes per language it is not distinguishable from noise.

### Parser corrections, made after the run

Two bugs in reading spreadsheet replies were found after the run and fixed. Every episode was re-scored from its saved reply with no new API calls (`eval/run_v2.py --rescore`). The as-run scores are kept in each folder's `results.as_run.csv`.
- **Decimal commas.** Portuguese answers like "57,93%" were read as 5793.
- **Verbose replies.** Claude ignored "reply with a single number" in 27 replies and counted aloud. The parser took the first number, a row id, instead of the final answer.

The parser now takes the last number and reads a lone comma followed by one or two digits as a decimal comma. That changed 13 outcomes, every one from wrong to correct and every one on a spreadsheet arm: 10 decimal-comma answers from Sol and Terra, 3 final answers from Sonnet. A3 `csv_documented` rose from 27% to 47% and A3 `csv_raw` from 5% to 7%. 36 more episodes kept their outcome but now show the reading the model actually gave. Re-scoring the main run with the same code changed nothing (0 of 3,240), so the correction touches only the spreadsheet arms.

### Pre-registered expectations, scored

| expectation (written before the run) | result |
|---|---|
| `csv_raw` at or below `raw_sql` on both questions | **held**: 43% vs 77%, 7% vs 62% |
| `csv_documented` gets definitions right but loses to counting; Sol and Terra high; Claude and the small models low; clearly below `documented_prose` | **held**: Sol and Terra 100%, Claude 0-20%, Luna 60%, gpt-oss 0-40%; 47-50% vs 100% |
| A3 harder than A1 for both CSV arms | **held for `csv_raw`** (43% to 7%); **barely for `csv_documented`** (50% to 47%) |
| Semantic layer about 100% on both, at about a tenth of a CSV call's input | **held**: 100%, 1,120 vs about 12,000 input tokens |
| No language effect | **mixed**: a 7-10 point gap, within noise at this sample size |

### What it adds to the picture

The main run found that a documented warehouse matches the semantic layer when the model writes SQL. This addition shows the gap that the "paste the spreadsheet" workflow leaves open:
- The same documentation that made SQL arms perfect brings a spreadsheet in context only to about 50%. The model has to do the arithmetic itself, and most models cannot count 300 rows reliably in one pass.
- Any interface where an engine executes the definition closes that gap: documented SQL and the semantic layer alike.
- Against the spreadsheet workflow, the semantic layer is a clear improvement on accuracy (100% vs 47-50%) and on cost per correct answer (27-49x). Against a documented warehouse queried with SQL, the main run's conclusion stands.
