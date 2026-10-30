"""Evaluation configuration. Edit here, nothing else needs to change.

Default volume: (4 + 3 + 3 + 3) arm cells x 6 models x 2 languages x 5 runs
= 780 API calls. Trim LANGUAGES, MODELS, or N_RUNS to cut cost.
"""

# Languages to run every question in. The scaffolding (instructions,
# schema, docs, Cube meta) stays in English, as it does in most Brazilian
# companies; only the business question changes language.
LANGUAGES = ["en", "pt"]

# Each question runs against its own set of arms and is scored against
# its own fingerprint set.
#   kind "count":   correct means exactly the governed number
#   kind "percent": correct means within TOLERANCE_PP of the governed share;
#                   answers <= 1.0 are treated as fractions and multiplied by 100
QUESTIONS = [
    {
        "id": "q1_active_count",
        "fp": "active_count",
        "kind": "count",
        "text": {
            "en": "How many active users do we have?",
            "pt": "Quantos usuários ativos nós temos?",
        },
        "arms": ["raw_sql", "documented_sql", "csv_in_context", "semantic_layer"],
    },
    {
        # Ratios are where curated views stop helping on their own: the
        # documented warehouse ships the numerator as a view, but the
        # denominator policy exists only as prose in the dictionary and
        # must be reconstructed into SQL on every query. The semantic
        # layer ships the whole ratio as one governed measure.
        "id": "q2_active_share",
        "fp": "active_share",
        "kind": "percent",
        "text": {
            "en": "What share of our users is active?",
            "pt": "Qual é o percentual de usuários ativos?",
        },
        "arms": ["raw_sql", "documented_sql", "semantic_layer"],
    },
    {
        # The join question. Fan-out, event-type filtering, population
        # exclusions, a TIMESTAMP boundary trap, and the behavioral vs
        # administrative confusion (MAU vs the status column) all stack.
        # No csv_in_context arm: 7,691 event rows do not fit the
        # spreadsheet path, which is itself part of the argument.
        "id": "q3_mau_june",
        "fp": "mau",
        "kind": "count",
        "text": {
            "en": "How many monthly active users did we have in June 2026?",
            "pt": "Quantos usuários ativos mensais tivemos em junho de 2026?",
        },
        "arms": ["raw_sql", "documented_sql", "semantic_layer"],
    },
    {
        # The second time grain. Every MAU trap reappears, and the week
        # adds its own: Monday vs Sunday week start, a Sunday boundary
        # bug, and ISO week numbers that repeat every year. June 22, 2026
        # is a Monday. The semantic layer pays for the new grain with one
        # measure; raw SQL pays with a larger interpretation space.
        "id": "q4_wau_week",
        "fp": "wau",
        "kind": "count",
        "text": {
            "en": "How many weekly active users did we have in the week of June 22, 2026?",
            "pt": "Quantos usuários ativos semanais tivemos na semana de 22 de junho de 2026?",
        },
        "arms": ["raw_sql", "documented_sql", "semantic_layer"],
    },
]

# Models to evaluate. Any OpenRouter slug works; see https://openrouter.ai/models
# Two tiers per vendor for cross-vendor divergence. GPT-6.1 Terra is
# price-matched to Haiku; GPT-6.1 Sol is OpenAI's tier below its flagship,
# the Sonnet slot. The -pro variants are skipped: same per-token rate,
# far more reasoning spend per call.
# Below them, two less capable models extend the capability gradient:
# GPT-6 Luna, OpenAI's small tier, and gpt-oss-20b, a 21B open-weight
# model as the floor. Anthropic has nothing below Haiku 4.5 on OpenRouter
# (Haiku 5.5 is its cheaper but stronger successor).
MODELS = [
    "anthropic/claude-haiku-4.5",
    "anthropic/claude-sonnet-4.6",
    "openai/gpt-6.1-terra",
    "openai/gpt-6.1-sol",
    "openai/gpt-6-luna",
    "openai/gpt-oss-20b",
]

# OpenRouter provider preferences, per model, sent as the request's
# "provider" field. Open-weight models are served by many hosts at
# different precisions, and unpinned routing mixes them call to call
# (fp4 and fp8 hosts produced corrupted SQL in testing). Restrict
# gpt-oss-20b to bf16 hosts so every run measures the same model. A
# single host is too fragile: DeepInfra alone returned upstream 429s.
PROVIDER_ROUTING = {
    "openai/gpt-oss-20b": {"quantizations": ["bf16"]},
}

# Repetitions per (question, language, arm, model). Variance across runs
# is part of the signal, so keep temperature at the API default (1.0)
# unless you are testing the effect of temperature itself. The OpenAI
# GPT-6 and GPT-6.1 models do not accept temperature; OpenRouter drops it
# and they sample at the provider default. gpt-oss-20b accepts it.
N_RUNS = 5
TEMPERATURE = 1.0
# A cap, not a target. The OpenAI models reason at their default effort
# and reasoning tokens count against this cap. On the CSV arm Terra spends
# 500-800 of them, Luna about 1,500, gpt-oss-20b 1,700-2,500, so a tight
# cap would truncate answers into errors. Claude runs without extended
# thinking and stops far below it.
MAX_TOKENS = 8000

# Matching tolerance for percent answers, in percentage points. The share
# fingerprints are separated by at least 0.06pp, so 0.02pp is unambiguous.
TOLERANCE_PP = 0.02

CUBE_URL = "http://localhost:4000"
# Dev mode accepts any bearer token; this value is irrelevant but required.
CUBE_TOKEN = "demo"

# USD per million tokens, OpenRouter list rates (no caching, no batch).
# Pinned from https://openrouter.ai/models: Anthropic July 2026 (unchanged
# when re-checked October 2026), OpenAI October 2026.
# Prices change; verify before quoting numbers from a run. Every model in
# MODELS must have an entry here or the run refuses to start.
# OpenAI prompt caching is automatic on OpenRouter (cache writes billed
# above the input rate, cache reads below it), so billed spend for the
# OpenAI-hosted rows will differ from these list-rate figures.
# gpt-oss-20b is priced at the bf16 hosts' rate (DeepInfra; DekaLLM bills
# $0.029 input), not the cheapest host's headline price.
PRICING = {
    "anthropic/claude-haiku-4.5": {"input_per_mtok": 1.00, "output_per_mtok": 5.00},
    "anthropic/claude-sonnet-4.6": {"input_per_mtok": 3.00, "output_per_mtok": 15.00},
    "openai/gpt-6.1-terra": {"input_per_mtok": 1.00, "output_per_mtok": 5.00},
    "openai/gpt-6.1-sol": {"input_per_mtok": 2.00, "output_per_mtok": 10.00},
    "openai/gpt-6-luna": {"input_per_mtok": 0.10, "output_per_mtok": 0.50},
    "openai/gpt-oss-20b": {"input_per_mtok": 0.03, "output_per_mtok": 0.14},
}

RESULTS_DIR = "results"
