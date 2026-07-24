"""Evaluation configuration. Edit here, nothing else needs to change.

Default volume: (4 + 3 + 3) arm cells x 2 models x 2 languages x 5 runs
= 200 API calls. Trim LANGUAGES, MODELS, or N_RUNS to cut cost.
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
]

# Models to evaluate. Wire additional providers in providers.py.
MODELS = [
    "claude-haiku-4-5-20251001",
    "claude-sonnet-4-6",
]

# Repetitions per (question, language, arm, model). Variance across runs
# is part of the signal, so keep temperature at the API default (1.0)
# unless you are testing the effect of temperature itself.
N_RUNS = 5
TEMPERATURE = 1.0
MAX_TOKENS = 800

# Matching tolerance for percent answers, in percentage points. The share
# fingerprints are separated by at least 0.06pp, so 0.02pp is unambiguous.
TOLERANCE_PP = 0.02

CUBE_URL = "http://localhost:4000"
# Dev mode accepts any bearer token; this value is irrelevant but required.
CUBE_TOKEN = "demo"

# USD per million tokens, standard API rates (no caching, no batch).
# Pinned July 2026 from https://platform.claude.com/docs/en/about-claude/pricing
# Prices change; verify before quoting numbers from a run. Every model in
# MODELS must have an entry here or the run refuses to start.
PRICING = {
    "claude-haiku-4-5-20251001": {"input_per_mtok": 1.00, "output_per_mtok": 5.00},
    "claude-sonnet-4-6": {"input_per_mtok": 3.00, "output_per_mtok": 15.00},
}

RESULTS_DIR = "results"
