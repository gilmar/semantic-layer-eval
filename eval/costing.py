"""Token accounting and cost math. No heavy dependencies, so it can be
unit-tested without duckdb or an API key.

Prices are pinned in eval/config.py per million tokens. The headline
metric is cost per correct answer: a cheap arm that answers wrong is an
expensive arm, because wrong numbers get reported, caught, and redone.
"""


def call_cost(pricing: dict, model: str,
              input_tokens: int, output_tokens: int) -> float:
    p = pricing[model]
    return (input_tokens * p["input_per_mtok"]
            + output_tokens * p["output_per_mtok"]) / 1_000_000


def aggregate(records: list) -> dict:
    calls = len(records)
    correct = sum(r["correct"] for r in records)
    tin = sum(r["input_tokens"] for r in records)
    tout = sum(r["output_tokens"] for r in records)
    cost = sum(r["cost_usd"] for r in records)
    return {
        "calls": calls,
        "correct": correct,
        "input_tokens": tin,
        "output_tokens": tout,
        "mean_input": (tin / calls) if calls else 0.0,
        "mean_output": (tout / calls) if calls else 0.0,
        "tokens_per_correct": ((tin + tout) / correct) if correct else None,
        "cost": cost,
        "wasted": sum(r["cost_usd"] for r in records if not r["correct"]),
        "cost_per_correct": (cost / correct) if correct else None,
    }


def fmt_usd(x) -> str:
    return "n/a" if x is None else f"${x:.4f}"
