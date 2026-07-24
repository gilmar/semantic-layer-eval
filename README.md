# Semantic Layer Eval

A runnable argument for one thesis: traditional database tables, even well modelled and well documented, cannot substitute a semantic layer for AI agents.

The experiment does not compare a semantic layer against a strawman. It compares it against two versions of the same warehouse. One is undocumented, the way most databases actually are. The other is the steelman: catalog comments on every column, a metrics dictionary that fully defines every official metric in prose, and curated views encoding the governed logic. Nothing is hidden and retrieval is perfect. If documentation could substitute for a semantic layer, the steelman is where it would happen.

## The two versions

`db/schema.py` is the single source of truth. Both the Cube-facing database and the harness environments are built from it, so the documentation a model sees is provably the documentation stored in the catalog.

**raw.users, raw.events** Two bare tables. Correct types, no comments, no dictionary, no views.

**modeled.users, modeled.events** The same rows plus:
- `COMMENT ON` for both tables and every column, stating explicitly that soft-deleted rows and test accounts must be excluded from all business metrics, that `plan` is independent of activity, that login recency is not part of any activity definition, that joining users to events multiplies rows, and which event types are passive or machine generated
- `metrics_dictionary`, a table defining `active_users`, `active_share`, `monthly_active_users`, `churned_users`, and `paying_users` in prose, with owners
- `vw_active_users`, a curated view encoding the official active-user numerator
- `vw_qualifying_events`, a curated fact view of qualifying product events by valid users

Cube sits on top of the modeled schema. The semantic layer does not replace good modelling; it is the layer good modelling still needs.

## The traps

`data/generate_data.py` produces 300 users and 7,691 events (single seed, fixed reference date). Users carry five confounders: a four-value `status` column, `is_deleted`, `is_test_account`, a `plan` column inviting a "paying = active" reading, and `last_login_at` inviting a "logged in recently = active" reading.

Events add five more: join fan-out (COUNT(*) vs COUNT(DISTINCT)), passive (`email_opened`) and machine (`webhook_ping`) events that must be excluded from qualifying activity, noisy test-account bots and deleted users with history, a TIMESTAMP `occurred_at` where `BETWEEN '2026-06-01' AND '2026-06-30'` silently drops most of June 30, and the behavioral-versus-administrative confusion between MAU and the `status` column. Four deterministic crafted user groups guarantee every interpretation stays numerically separable.

Governed definitions. Active user: `status = 'active' AND is_deleted = false AND is_test_account = false`, which yields **157** out of a reporting universe of **271**, so the governed active share is **57.93%**. MAU for month M: distinct valid users with at least one qualifying event in calendar month M, which for June 2026 yields **160**.

Every plausible interpretation produces a distinct value, so an answer fingerprints the definition the model silently chose:

| count | active-users interpretation |
|---|---|
| 300 | total rows |
| 281 | any status, excluding deleted |
| 271 | reporting universe (excluding deleted and test) |
| 211 | active or pending |
| 190 | active or pending, excluding deleted and test |
| 174 | status = 'active' only |
| 165 | active, excluding test |
| 164 | active, excluding deleted |
| **157** | **governed definition (ground truth)** |
| 149 | any paying plan |
| 87 | paying and active |

| count | MAU June 2026 interpretation |
|---|---|
| 2391 | event rows in June (fan-out, all types) |
| 1545 | qualifying event rows by valid users (fan-out) |
| 184 | distinct users, all events, everyone included |
| 182 | distinct users, qualifying events, test and deleted included |
| 174 | status = 'active' count (not MAU at all) |
| 162 | valid users, passive events included |
| **160** | **governed MAU (ground truth)** |
| 158 | governed filters but BETWEEN boundary bug (June 30 lost) |
| 157 | governed active-user count (not MAU at all) |
| 155 | governed MAU additionally requiring status = 'active' |

For the share question, the twelve plausible numerator/denominator combinations range from 52.33% to 64.21% and are separated by at least 0.066 percentage points, so the harness matches at 0.02pp tolerance without ambiguity. Login-recency interpretations are computed live at eval time since they depend on the current date.

## Questions and arms

**Q1: "How many active users do we have?"** runs on four arms:

1. `raw_sql`. Undocumented version. Schema, profiling facts, sample rows. Model writes SQL, harness executes it.
2. `documented_sql`. Modeled version. The prompt is rendered live from the catalog: comments, full dictionary contents, view DDL. Model writes SQL, harness executes it against the modeled environment (views available).
3. `csv_in_context`. Full users CSV in the prompt, model answers directly. Arithmetic unreliability stacked on definitional ambiguity.
4. `semantic_layer`. Harness pulls Cube `/meta`, the model emits a JSON Cube query, Cube executes it.

**Q2: "What share of our users is active?"** runs on the two SQL arms and the semantic layer. This is where the documented steelman meets its structural limit. The curated view hands the model the numerator. The denominator policy exists only as prose in the dictionary and must be reconstructed into SQL on every single query. Composition is exactly what documentation cannot execute. On the Cube side, `active_share` ships as one governed measure.

**Q3: "How many monthly active users did we have in June 2026?"** runs on the two SQL arms and the semantic layer. The join question. Even with `vw_qualifying_events` handing the documented arm a clean fact view, the model still chooses DISTINCT or fan-out, still writes the date range, and still risks the BETWEEN boundary bug, because docs describe and views pre-filter but neither executes the metric. On the Cube side, `monthly_active_users` is a `count_distinct` measure over the curated view, and Cube date ranges are end-inclusive whole days, so the boundary bug cannot happen there by construction. There is no CSV arm: 7,691 event rows do not fit the spreadsheet path, which is itself part of the argument.

Each (question, language, arm, model) cell runs five times at temperature 1.0. Two metrics: correctness against the governed value, and consistency (distinct answers per cell).

## Portuguese variants

Every question also runs in Portuguese (`LANGUAGES` in `eval/config.py`). The scaffolding stays in English, as warehouses in Brazilian companies usually do: schema, comments, dictionary, and Cube descriptions are English; only the business question changes. The semantic arm reduces to measure selection plus deterministic execution, so it is structurally language-invariant. The SQL arms must bridge translation, definition reconstruction, and SQL synthesis on every query, so the pt rows measure how much interpretation variance the language boundary adds on top of the definitional ambiguity.

## The semantic arm protocol

The model emits a small JSON Cube query: one measure, optionally a time dimension with a date range. That mirrors how agents actually talk to a semantic layer over MCP, and it is what makes the June question answerable through the layer. The decision surface is still tiny: pick the described official measure, name the month. Definition, joins, population exclusions, event filtering, and date semantics all execute inside Cube.

## Quickstart

Requirements: [uv](https://docs.astral.sh/uv/), Python 3.10+, Docker Desktop, an Anthropic API key. Same commands in PowerShell.

```
uv sync

# 1. Generate the data (prints both fingerprint tables)
uv run python data/generate_data.py

# 2. Build demo.duckdb with BOTH versions (raw and modeled schemas)
uv run python db/load_duckdb.py

# 3. Start the semantic layer (playground at http://localhost:4000)
docker compose up -d

# 4. Run the eval
set ANTHROPIC_API_KEY=sk-ant-...        (PowerShell: $env:ANTHROPIC_API_KEY="sk-ant-...")
uv run python eval/run_eval.py
```

Defaults produce 200 API calls (10 arm cells x 2 models x 2 languages x 5 runs), roughly one US dollar at pinned rates, dominated by the CSV arm on Sonnet. Trim `LANGUAGES`, `MODELS`, or `N_RUNS` in `eval/config.py` to cut cost. Outputs land in `results/results.csv` (every run with raw responses, token counts, and per-call cost) and `results/report.md` (per-question summary tables, the token footprint and cost analyses, and all three fingerprint tables).

## Design notes

The harness never opens `db/demo.duckdb`. Cube holds that file read-write and DuckDB allows one writer per file, so the harness rebuilds both environments in memory from the CSVs via the same `db/schema.py`. Same rows, same catalog, no lock contention.

Perfect retrieval in the documented arm is deliberate. It biases the experiment against the thesis. Any gap that survives it is structural, not an artifact of missing or badly written docs.

Everything is deterministic where it should be: single seed, fixed reference date, distinctness asserted at generation time, crafted separator users selected by deterministic rules. The only stochastic component is the models, which is the thing under measurement.

## Token footprint

Token usage is reported independently of cost, because tokens are the structural property of each interface while prices are a moving multiplier. Three reasons the footprint matters on its own. First, price invariance: rates change and differ across vendors, but what each interface forces you to ship does not. Second, agentic headroom: every token spent asking a data question is context an agent cannot spend on tool schemas, conversation history, or reasoning, so the per-question footprint decides how long an agent session stays viable. Third, throughput: rate limits are denominated in tokens per minute, so footprint times question volume sets the org ceiling.

The report shows input and output tokens per call by question and arm, with an input-versus-semantic ratio inside each question, plus a rollup with **tokens per correct answer**. The structural pattern: the SQL arms re-ship schema, docs, and samples on every question, the CSV arm re-ships the entire dataset, and the semantic arm ships a measure list. Note the scaling asymmetry behind the documented arm's number: its footprint grows with the size of the catalog, because all of it travels on every query, while the semantic layer's meta grows only with the measure list. Documenting more of the warehouse makes the documented arm heavier; the semantic arm barely changes.

## Cost analysis

Every call is priced from API-reported usage at the standard rates pinned in `eval/config.py`, sourced from the official pricing page in July 2026. Rates change; update `PRICING` before quoting numbers, and the run refuses to start if a configured model has no pricing entry. Calls whose SQL or JSON fails to resolve are still priced, because wrong answers cost money too.

The report gives cost by arm and model and by question and arm: calls, correct, total spend, wasted spend (runs that did not produce the governed answer), and the headline metric, **cost per correct answer**. Per-call cost rewards the CSV arm for being a single round trip; per-correct cost exposes what the spend actually buys. When accuracy differences stack on top of the footprints, the cost-per-correct spread is usually wider than the accuracy spread alone.

Scope this honestly when presenting. The numbers are marginal inference cost per question only. They exclude Cube infrastructure and the engineering cost of building the layer, which is the right comparison for the per-question economics claim but not a TCO claim. The single-shot design is again biased against the thesis: real raw-SQL agents explore schemas over multiple turns (DISTINCT probes, sampling, retries), multiplying input tokens, while this benchmark hands them the profiling for free. No prompt caching or batch discounts are applied; both would lower absolute numbers, and caching would help the big-prompt arms most, but neither changes who has to ship how much context to answer one question.

## How to read the expected result

The documented arm will likely score well on Q1, and that is the honest outcome. It is also the point. Documentation describes; it still leaves definition-to-SQL reconstruction with the model on every query, which is why Q2, Q3, the pt rows, and the consistency columns matter. Even against perfect docs and curated views, the structural differences remain: a semantic layer executes the definition from a versioned artifact instead of re-deriving it, composes governed metrics (the ratio and the MAU are each one measure, not a per-query reconstruction), makes whole bug classes unrepresentable (fan-out, date boundaries), stays invariant to the language of the question, propagates a definition change to every consumer at once, and can deny raw access outright. Docs are advice. The layer is enforcement outside the model.

## What this toy does not show

Two tables, three questions, one join. Real ambiguity compounds further: multi-hop joins, timezones, currency, fiscal calendars, revenue recognition. Each adds another silent choice per query. The demo isolates the mechanism; production multiplies it.

## Extensions

Add an OpenAI or Gemini branch in `providers.py` for cross-vendor divergence. Add a second time grain (weekly active users) to show that each new grain multiplies the raw-SQL interpretation space while adding one measure to the layer.
