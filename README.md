# Semantic Layer Eval

A runnable argument for one thesis: traditional database tables, even well modelled and well documented, cannot substitute a semantic layer for AI agents.

The experiment does not compare a semantic layer against a strawman. It compares it against two versions of the same warehouse. One is undocumented, the way most databases actually are. The other is the steelman: catalog comments on every column, a metrics dictionary that fully defines every official metric in prose, and curated views encoding the governed logic. Nothing is hidden and retrieval is perfect. If documentation could substitute for a semantic layer, the steelman is where it would happen.

## The two versions

`db/schema.py` is the single source of truth. Both the Cube-facing database and the harness environments are built from it, so the documentation a model sees is provably the documentation stored in the catalog.

**raw.users, raw.events** Two bare tables. Correct types, no comments, no dictionary, no views.

**modeled.users, modeled.events** The same rows plus:
- `COMMENT ON` for both tables and every column, stating explicitly that soft-deleted rows and test accounts must be excluded from all business metrics, that `plan` is independent of activity, that login recency is not part of any activity definition, that joining users to events multiplies rows, and which event types are passive or machine generated
- `metrics_dictionary`, a table defining `active_users`, `active_share`, `monthly_active_users`, `weekly_active_users`, `engaged_users`, `churned_users`, and `paying_users` in prose, with owners
- `vw_active_users`, a curated view encoding the official active-user numerator
- `vw_qualifying_events`, a curated fact view of qualifying product events by valid users

Cube sits on top of the modeled schema. The semantic layer does not replace good modelling; it is the layer good modelling still needs.

## The traps

`data/generate_data.py` produces 300 users and 7,691 events (single seed, fixed reference date). Users carry five confounders: a four-value `status` column, `is_deleted`, `is_test_account`, a `plan` column inviting a "paying = active" reading, and `last_login_at` inviting a "logged in recently = active" reading.

Events add five more: join fan-out (COUNT(*) vs COUNT(DISTINCT)), passive (`email_opened`) and machine (`webhook_ping`) events that must be excluded from qualifying activity, noisy test-account bots and deleted users with history, a TIMESTAMP `occurred_at` where `BETWEEN '2026-06-01' AND '2026-06-30'` silently drops most of June 30, and the behavioral-versus-administrative confusion between MAU and the `status` column. Deterministic crafted separators (event groups A to E and one plan swap, documented in the generator) guarantee every interpretation stays numerically separable.

The weekly grain inherits all five and adds its own: Monday- versus Sunday-start weeks, a Sunday boundary bug (`BETWEEN '2026-06-22' AND '2026-06-28'`), the same bug on the Sunday-start week, ISO week numbers matched without a year (`week(occurred_at) = 26` also counts 2023 to 2025), and answering WAU with the month's MAU.

Governed definitions. Active user: `status = 'active' AND is_deleted = false AND is_test_account = false`, which yields **157** out of a reporting universe of **271**, so the governed active share is **57.93%**. MAU for month M: distinct valid users with at least one qualifying event in calendar month M, which for June 2026 yields **160**. WAU for week W: the same over ISO week W, Monday 00:00 through the end of Sunday, which for the week of Monday June 22, 2026 yields **136**.

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

| count | WAU week of June 22, 2026 interpretation |
|---|---|
| 589 | event rows in the week (fan-out, all types) |
| 382 | qualifying event rows by valid users (fan-out) |
| 174 | status = 'active' count (not WAU at all) |
| 165 | distinct users, all events, everyone included |
| 160 | governed June MAU (wrong grain) |
| 157 | governed active-user count (not WAU at all) |
| 155 | distinct users, qualifying events, test and deleted included |
| 146 | ISO week 26 matched in every year |
| 144 | valid users, passive events included |
| 141 | status = 'active' valid users, passive events included |
| 138 | Sunday-start week (June 21 to 27) |
| **136** | **governed WAU (ground truth)** |
| 133 | governed WAU additionally requiring status = 'active' |
| 132 | Sunday-start week with BETWEEN boundary bug |
| 131 | governed filters but BETWEEN boundary bug (Sunday June 28 lost) |

The MAU question has ten fingerprints; the WAU question has fifteen. In the layer, the new grain costs one `weekly_active_users` measure over the cube that already serves MAU.

For the share question, the twelve plausible numerator/denominator combinations range from 52.33% to 64.21% and are separated by at least 0.066 percentage points, so the harness matches at 0.02pp tolerance without ambiguity. Login-recency interpretations are computed live at eval time since they depend on the current date.

## Questions and arms

**Q1: "How many active users do we have?"** runs on four arms:

1. `raw_sql`. Undocumented version. Schema, profiling facts, sample rows. Model writes SQL, harness executes it.
2. `documented_sql`. Modeled version. The prompt is rendered live from the catalog: comments, full dictionary contents, view DDL. Model writes SQL, harness executes it against the modeled environment (views available).
3. `csv_in_context`. Full users CSV in the prompt, model answers directly. Arithmetic unreliability stacked on definitional ambiguity.
4. `semantic_layer`. Harness pulls Cube `/meta`, the model emits a JSON Cube query, Cube executes it.

**Q2: "What share of our users is active?"** runs on the two SQL arms and the semantic layer. This is where the documented steelman meets its structural limit. The curated view hands the model the numerator. The denominator policy exists only as prose in the dictionary and must be reconstructed into SQL on every single query. Composition is exactly what documentation cannot execute. On the Cube side, `active_share` ships as one governed measure.

**Q3: "How many monthly active users did we have in June 2026?"** runs on the two SQL arms and the semantic layer. The join question. Even with `vw_qualifying_events` handing the documented arm a clean fact view, the model still chooses DISTINCT or fan-out, still writes the date range, and still risks the BETWEEN boundary bug, because docs describe and views pre-filter but neither executes the metric. On the Cube side, `monthly_active_users` is a `count_distinct` measure over the curated view, and Cube date ranges are end-inclusive whole days, so the boundary bug cannot happen there by construction. There is no CSV arm: 7,691 event rows do not fit the spreadsheet path, which is itself part of the argument.

**Q4: "How many weekly active users did we have in the week of June 22, 2026?"** runs on the two SQL arms and the semantic layer. The second time grain. Every Q3 trap comes back at the new grain, and the week adds choices a month does not have: which day starts the week, where Sunday ends, and whether a week number needs a year. The documented arm gets the ISO-week policy in the dictionary, but it still has to turn that prose into date arithmetic on every query. On the Cube side, `weekly_active_users` is one more `count_distinct` measure on the same cube; the model names Monday to Sunday and Cube does the rest.

Each (question, language, arm, model) cell runs five times at temperature 1.0. Two metrics: correctness against the governed value, and consistency (distinct answers per cell).

Six models from two vendors, spanning a capability gradient: Claude Haiku 4.5 and Sonnet 4.6, GPT-6.1 Terra and Sol, and two less capable models below them, GPT-6 Luna (OpenAI's small tier) and gpt-oss-20b (a 21B open-weight model, the floor). Anthropic offers nothing below Haiku 4.5 on OpenRouter; Haiku 5.5 is cheaper but stronger. The gradient tests whether the semantic layer's advantage widens as model capability drops: a smaller decision surface should matter most to the models least able to reconstruct definitions on their own.

Each model runs at its vendor default, and the defaults differ. The GPT-6 and GPT-6.1 models do not accept a temperature parameter, so they sample at OpenAI's default. The OpenAI models also reason before answering: little on the SQL and semantic arms, and 500 to 2,500 hidden tokens on the CSV arm depending on the model. Claude runs without extended thinking. Keep both in mind when comparing consistency and CSV accuracy across vendors. Reasoning tokens are billed as output, so the token and cost tables include them.

gpt-oss-20b is served by many OpenRouter hosts at different precisions, and unpinned routing switches hosts call to call; in testing an fp8 host emitted corrupted SQL. `PROVIDER_ROUTING` in `eval/config.py` restricts it to bf16 hosts so every run measures the same model.

## Portuguese variants

Every question also runs in Portuguese (`LANGUAGES` in `eval/config.py`). The scaffolding stays in English, as warehouses in Brazilian companies usually do: schema, comments, dictionary, and Cube descriptions are English; only the business question changes. The semantic arm reduces to measure selection plus deterministic execution, so it is structurally language-invariant. The SQL arms must bridge translation, definition reconstruction, and SQL synthesis on every query, so the pt rows measure how much interpretation variance the language boundary adds on top of the definitional ambiguity.

## The semantic arm protocol

The model emits a small JSON Cube query: one measure, optionally a time dimension with a date range. That mirrors how agents actually talk to a semantic layer over MCP, and it is what makes the June question answerable through the layer. The decision surface is still tiny: pick the described official measure, name the month. Definition, joins, population exclusions, event filtering, and date semantics all execute inside Cube.

## Quickstart

Requirements: [uv](https://docs.astral.sh/uv/), Python 3.10+, Docker Desktop, an [OpenRouter](https://openrouter.ai/) API key. Same commands in PowerShell.

```
uv sync

# 1. Generate the data (prints both fingerprint tables)
uv run python data/generate_data.py

# 2. Build demo.duckdb with BOTH versions (raw and modeled schemas)
uv run python db/load_duckdb.py

# 3. Start the semantic layer (playground at http://localhost:4000)
docker compose up -d

# 4. Put your OpenRouter key in .env, then run the eval
#    cp .env.example .env   # if needed, then edit OPENROUTER_API_KEY=
uv run python eval/run_eval.py
```

Defaults produce 780 API calls (13 arm cells x 6 models x 2 languages x 5 runs), roughly 1.80 US dollars at pinned list rates (estimated from prompt sizes and observed output lengths), dominated by the CSV arm on Sonnet; the two less capable models add about six cents. Trim `LANGUAGES`, `MODELS`, or `N_RUNS` in `eval/config.py` to cut cost. Outputs land in `results/results.csv` (every run with raw responses, token counts, and per-call cost) and `results/report.md` (per-question summary tables, the token footprint and cost analyses, and all four fingerprint tables).

## Design notes

The harness never opens `db/demo.duckdb`. Cube holds that file read-write and DuckDB allows one writer per file, so the harness rebuilds both environments in memory from the CSVs via the same `db/schema.py`. Same rows, same catalog, no lock contention.

Perfect retrieval in the documented arm is deliberate. It biases the experiment against the thesis. Any gap that survives it is structural, not an artifact of missing or badly written docs.

Everything is deterministic where it should be: single seed, fixed reference date, distinctness asserted at generation time, crafted separator users selected by deterministic rules. The only stochastic component is the models, which is the thing under measurement.

## Token footprint

Token usage is reported independently of cost, because tokens are the structural property of each interface while prices are a moving multiplier. Three reasons the footprint matters on its own. First, price invariance: rates change and differ across vendors, but what each interface forces you to ship does not. Second, agentic headroom: every token spent asking a data question is context an agent cannot spend on tool schemas, conversation history, or reasoning, so the per-question footprint decides how long an agent session stays viable. Third, throughput: rate limits are denominated in tokens per minute, so footprint times question volume sets the org ceiling.

The report shows input and output tokens per call by question and arm, with an input-versus-semantic ratio inside each question, plus a rollup with **tokens per correct answer**. The structural pattern: the SQL arms re-ship schema, docs, and samples on every question, the CSV arm re-ships the entire dataset, and the semantic arm ships a measure list. Note the scaling asymmetry behind the documented arm's number: its footprint grows with the size of the catalog, because all of it travels on every query, while the semantic layer's meta grows only with the measure list. Documenting more of the warehouse makes the documented arm heavier; the semantic arm barely changes.

## Cost analysis

Every call is priced from API-reported usage at the OpenRouter list rates pinned in `eval/config.py`, sourced from openrouter.ai/models (Anthropic in July 2026, OpenAI in October 2026). Rates change; update `PRICING` before quoting numbers, and the run refuses to start if a configured model has no pricing entry. Calls whose SQL or JSON fails to resolve are still priced, because wrong answers cost money too. One vendor difference: OpenRouter applies OpenAI prompt caching automatically, billing cache writes above the input rate and cache reads below it, so your OpenRouter bill for the OpenAI-hosted rows will not match the report. The report keeps list rates for every model so the arms and vendors stay comparable. gpt-oss-20b is priced at its bf16 hosts' rate, not the cheaper headline price of its lowest-precision host.

The report gives cost by arm and model and by question and arm: calls, correct, total spend, wasted spend (runs that did not produce the governed answer), and the headline metric, **cost per correct answer**. Per-call cost rewards the CSV arm for being a single round trip; per-correct cost exposes what the spend actually buys. When accuracy differences stack on top of the footprints, the cost-per-correct spread is usually wider than the accuracy spread alone.

Scope this honestly when presenting. The numbers are marginal inference cost per question only. They exclude Cube infrastructure and the engineering cost of building the layer, which is the right comparison for the per-question economics claim but not a TCO claim. The single-shot design is again biased against the thesis: real raw-SQL agents explore schemas over multiple turns (DISTINCT probes, sampling, retries), multiplying input tokens, while this benchmark hands them the profiling for free. No prompt caching or batch discounts are applied; both would lower absolute numbers, and caching would help the big-prompt arms most, but neither changes who has to ship how much context to answer one question.

## How to read the expected result

The documented arm will likely score well on Q1, and that is the honest outcome. It is also the point. Documentation describes; it still leaves definition-to-SQL reconstruction with the model on every query, which is why Q2, Q3, Q4, the pt rows, and the consistency columns matter. Even against perfect docs and curated views, the structural differences remain: a semantic layer executes the definition from a versioned artifact instead of re-deriving it, composes governed metrics (the ratio, the MAU, and the WAU are each one measure, not a per-query reconstruction), makes whole bug classes unrepresentable (fan-out, date boundaries), stays invariant to the language of the question, propagates a definition change to every consumer at once, and can deny raw access outright. Docs are advice. The layer is enforcement outside the model.

## Steelman v2

v1's documented arm passed every smoke call because its curated views were written knowing the questions: each one is a `COUNT(DISTINCT)` over a view that already encodes the definition. v2 (spec: `docs/steelman-v2-spec.md`) holds the authored artifacts fixed and asks compositions of them:
- one dictionary row and one Cube measure per metric (the new one is `engaged_users`);
- the two v1 views, frozen;
- one structural Cube join, `product_events -> users`.

It adds a prose-only arm (`documented_prose`, comments and dictionary with no views) and an agentic mode, where SQL agents may probe and retry with `run_sql` and the semantic agent uses `cube_load`.

| category | question | what it tests |
|---|---|---|
| A | active users; June MAU | controls |
| B | June MAU on the pro plan | a dimension on the other table |
| C | share of paying users active in June | a ratio no artifact defines |
| D | June MAU by plan | a breakdown |
| E | MAU per month of Q2; WAU for the week of June 22 | time grains and boundaries |
| F | engaged users in June | a threshold metric defined only in prose and as a measure |

Answers are scored `correct`, `silent_wrong`, or `loud_error`. The report evaluates three pre-registered falsifiers:
- F1: views match the layer.
- F2: prose alone matches the layer.
- F3: the layer cannot compose an undefined ratio.

```
uv run python eval/run_v2.py                     # first pass: 1,296 episodes, about $7.50
uv run python eval/run_v2.py --model openai/gpt-6-luna --lang en --runs 1   # smoke, about $0.02
```

Outputs go to `results/v2/`: `results.csv`, `report.md`, and one transcript per episode. Before any API call, the runner checks that its DuckDB fingerprints match the generator's. v2 adds the `engaged_users` metric to both sides, so v1's documented prompt gains one dictionary row and v1's semantic prompt gains one measure. v1's fingerprints and governed answers are unchanged.

## What this toy does not show

Two tables, four questions, one join, two time grains. Real ambiguity compounds further: multi-hop joins, timezones, currency, fiscal calendars, revenue recognition. Each adds another silent choice per query. The demo isolates the mechanism; production multiplies it.

## Extensions

Add Gemini OpenRouter slugs to `MODELS` in `eval/config.py` (and matching `PRICING` entries) for a third vendor.
