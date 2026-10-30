"""Steelman v2 configuration (docs/steelman-v2-spec.md).

Models, pricing, provider routing, sampling, and the Cube endpoint are
shared with v1 (eval/config.py) so both versions price and route every
model identically. Everything specific to v2 lives here.

Full-run volume with the defaults below. The 2026-10-29 run covered the
original 3,240 episodes ($14.35 at list rates); the spreadsheet addition
(A3 on every single-shot arm, the two CSV arms on A1) adds 480 calls:
  single-shot: 8 questions x 4 arms + 2 questions x 2 CSV arms + A3 x 4 arms
               = 40 arm cells x 6 models x 2 languages x 5 runs = 2,400 calls
  agentic:     (8 x 2 + 3 x 2) arm cells x 6 models x 2 languages x 5 runs = 1,320 episodes

The spec's cheaper first pass (Sonnet, Haiku, Luna, gpt-oss-20b at three
runs) is the same run filtered with --model and --runs 3.
"""

import config as v1

LANGUAGES = ["en", "pt"]

# Full run: the whole capability gradient, both vendors, five runs per
# cell. Every model must be priced in eval/config.py.
MODELS = [
    "anthropic/claude-sonnet-4.6",
    "openai/gpt-6.1-sol",
    "anthropic/claude-haiku-4.5",
    "openai/gpt-6.1-terra",
    "openai/gpt-6-luna",
    "openai/gpt-oss-20b",
]
N_RUNS = 5

# Agentic mode: tool calls allowed before the harness forces an answer.
MAX_TOOL_CALLS = 8
# Rows returned to the agent per query (the full count is reported too).
TOOL_ROW_LIMIT = 20

# Parallel episodes. Each builds its own in-memory DuckDB, so workers do
# not share state; the limit is OpenRouter and Cube throughput.
WORKERS = 8

PRICING = v1.PRICING
PROVIDER_ROUTING = v1.PROVIDER_ROUTING
TEMPERATURE = v1.TEMPERATURE
MAX_TOKENS = v1.MAX_TOKENS
TOLERANCE_PP = v1.TOLERANCE_PP
CUBE_URL = v1.CUBE_URL
CUBE_TOKEN = v1.CUBE_TOKEN

RESULTS_DIR = "results/v2"

SQL_ARMS = ["raw_sql", "documented_prose", "documented_views"]
CORE_ARMS = SQL_ARMS + ["semantic_layer"]
# Spreadsheet-in-context arms (post-run addition, see the spec): the whole
# users.csv in the prompt, with no documentation or with the users column
# notes and full metrics dictionary. Single-shot only, and only on
# questions users.csv alone can answer.
CSV_ARMS = ["csv_raw", "csv_documented"]
ARM_ORDER = CORE_ARMS + CSV_ARMS
# Agentic coverage per the spec: views and the layer everywhere, the two
# weaker arms on categories A and B only, to bound cost.
AGENTIC_ALL = ["documented_views", "semantic_layer"]

MODES = ["single_shot", "agentic"]

# kind "count":   exact integer match
# kind "percent": within TOLERANCE_PP; answers <= 1.0 are read as fractions
# kind "vector":  a {group: number} breakdown; every cell must match exactly
#
# "fp" names the block in data/fingerprints.json. "decisions" is the static
# count of silent choices a correct answer requires per arm (spec table,
# re-derived against the final prompts; see the spec's decision section).
# A per-mode dict overrides a plain number.
QUESTIONS = [
    {
        "id": "A1_active_users", "category": "A", "fp": "active_count", "kind": "count",
        "text": {
            "en": "How many active users do we have?",
            "pt": "Quantos usuários ativos nós temos?",
        },
        "single_shot_arms": ARM_ORDER,
        "agentic_arms": CORE_ARMS,
        "decisions": {"raw_sql": 3, "documented_prose": 3, "documented_views": 1,
                      "semantic_layer": 1, "csv_raw": 3, "csv_documented": 3},
    },
    {
        # Post-run addition (v1's Q2), for the spreadsheet comparison: the
        # ratio both sides define (dictionary row active_share, Cube measure
        # users.active_share). Single-shot only.
        "id": "A3_active_share", "category": "A", "fp": "active_share", "kind": "percent",
        "text": {
            "en": "What share of our users is active?",
            "pt": "Qual é o percentual de usuários ativos?",
        },
        "single_shot_arms": ARM_ORDER,
        "agentic_arms": [],
        "decisions": {"raw_sql": 5, "documented_prose": 5, "documented_views": 3,
                      "semantic_layer": 1, "csv_raw": 5, "csv_documented": 5},
    },
    {
        "id": "A2_mau_june", "category": "A", "fp": "mau_june_2026", "kind": "count",
        "text": {
            "en": "How many monthly active users did we have in June 2026?",
            "pt": "Quantos usuários ativos mensais tivemos em junho de 2026?",
        },
        "agentic_arms": CORE_ARMS,
        "decisions": {"raw_sql": 6, "documented_prose": 6, "documented_views": 2,
                      "semantic_layer": 2},
    },
    {
        "id": "B1_pro_mau_june", "category": "B", "fp": "b1_pro_mau_june_2026", "kind": "count",
        "text": {
            "en": "How many monthly active users on the pro plan did we have in June 2026?",
            "pt": "Quantos usuários ativos mensais do plano pro tivemos em junho de 2026?",
        },
        "agentic_arms": CORE_ARMS,
        "decisions": {"raw_sql": 7, "documented_prose": 7, "documented_views": 4,
                      "semantic_layer": 3},
    },
    {
        "id": "C1_paying_active_share", "category": "C",
        "fp": "c1_paying_active_share_june_2026", "kind": "percent",
        "text": {
            "en": "What share of paying users were monthly active in June 2026?",
            "pt": "Qual percentual dos usuários pagantes foi ativo mensal em junho de 2026?",
        },
        "agentic_arms": AGENTIC_ALL,
        "decisions": {"raw_sql": 10, "documented_prose": 10, "documented_views": 6,
                      "semantic_layer": {"single_shot": None, "agentic": 3}},
    },
    {
        "id": "D1_mau_by_plan", "category": "D", "fp": "d1_mau_by_plan_june_2026",
        "kind": "vector",
        "text": {
            "en": "Break down June 2026 monthly active users by plan.",
            "pt": "Detalhe os usuários ativos mensais de junho de 2026 por plano.",
        },
        "agentic_arms": AGENTIC_ALL,
        "decisions": {"raw_sql": 7, "documented_prose": 7, "documented_views": 4,
                      "semantic_layer": 3},
    },
    {
        "id": "E1_mau_by_month_q2", "category": "E", "fp": "e1_mau_by_month_q2_2026",
        "kind": "vector",
        "text": {
            "en": "What was monthly active users for each month of Q2 2026?",
            "pt": "Quantos foram os usuários ativos mensais em cada mês do segundo trimestre de 2026?",
        },
        "agentic_arms": AGENTIC_ALL,
        "decisions": {"raw_sql": 7, "documented_prose": 7, "documented_views": 3,
                      "semantic_layer": 3},
    },
    {
        "id": "E2_wau_week", "category": "E", "fp": "wau_week_of_2026_06_22", "kind": "count",
        "text": {
            "en": "How many weekly active users did we have in the week of June 22, 2026?",
            "pt": "Quantos usuários ativos semanais tivemos na semana de 22 de junho de 2026?",
        },
        "agentic_arms": AGENTIC_ALL,
        "decisions": {"raw_sql": 8, "documented_prose": 8, "documented_views": 3,
                      "semantic_layer": 2},
    },
    {
        "id": "F1_engaged_users", "category": "F", "fp": "f1_engaged_users_june_2026",
        "kind": "count",
        "text": {
            "en": "How many engaged users did we have in June 2026?",
            "pt": "Quantos usuários engajados tivemos em junho de 2026?",
        },
        "agentic_arms": AGENTIC_ALL,
        "decisions": {"raw_sql": 8, "documented_prose": 8, "documented_views": 3,
                      "semantic_layer": 2},
    },
]

# Ground-truth key per fingerprint block.
GROUND_TRUTH_KEYS = {
    "active_count": "governed_active_users",
    "active_share": "governed_active / valid_universe",
    "mau_june_2026": "governed_mau",
    "wau_week_of_2026_06_22": "governed_wau",
    "b1_pro_mau_june_2026": "governed_pro_mau",
    "c1_paying_active_share_june_2026": "governed_paid_mau / valid_paid_users",
    "d1_mau_by_plan_june_2026": "governed_mau_by_plan",
    "e1_mau_by_month_q2_2026": "governed_mau_by_month",
    "f1_engaged_users_june_2026": "governed_engaged_users",
}

# Falsifier thresholds, fixed before the first run (spec, "Thesis").
# F1/F2 "match": the documented arm's accuracy is within this many
# percentage points of the layer's (or above it) AND its mean distinct
# answers per cell is within DISTINCT_MARGIN of the layer's (or below it).
ACCURACY_MARGIN_PP = 5.0
DISTINCT_MARGIN = 0.25
# F3 "recovers": agentic semantic accuracy on category C at or above this.
RECOVERY_THRESHOLD = 0.8
