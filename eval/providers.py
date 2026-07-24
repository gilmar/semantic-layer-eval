"""Model providers via OpenRouter.

One function: complete(model, prompt) -> reply dict.

The reply must carry token usage so the harness can price every call:

    {"text": str, "input_tokens": int, "output_tokens": int}

All models go through OpenRouter's OpenAI-compatible chat completions
API. Put any OpenRouter model slug in MODELS (eval/config.py); no
per-vendor branch is needed. If a response omits usage, tokens are
returned as zero and the cost columns will be wrong for that call.
Keep it single-turn: the point of the experiment is what a model does
with one shot at an ambiguous question, not how well it can
interrogate the schema over ten turns.
"""

import os
from pathlib import Path

import requests
from dotenv import load_dotenv

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def complete(model: str, prompt: str, temperature: float, max_tokens: int) -> dict:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise SystemExit(
            "Set OPENROUTER_API_KEY in .env (see .env.example) before running the eval."
        )

    resp = requests.post(
        OPENROUTER_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=120,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"OpenRouter {resp.status_code}: {resp.text[:500]}")

    data = resp.json()
    text = data["choices"][0]["message"].get("content") or ""
    usage = data.get("usage") or {}
    return {
        "text": text,
        "input_tokens": int(usage.get("prompt_tokens") or 0),
        "output_tokens": int(usage.get("completion_tokens") or 0),
    }
