"""Model providers via OpenRouter.

Two functions:
  complete(model, prompt) -> {"text", "input_tokens", "output_tokens"}
      one user turn, one reply (v1 and the v2 single-shot mode)
  chat(model, messages, tools=...) -> {"message", "input_tokens",
      "output_tokens", "finish_reason"}
      one turn of a multi-turn conversation with optional tool calling
      (the v2 agentic mode)

Replies carry token usage so the harness can price every call.

All models go through OpenRouter's OpenAI-compatible chat completions
API. Put any OpenRouter model slug in MODELS (eval/config.py); no
per-vendor branch is needed. Models served by several hosts can be
pinned with PROVIDER_ROUTING, passed through as OpenRouter's "provider"
preferences. If a response omits usage, tokens are returned as zero and
the cost columns will be wrong for that call.
"""

import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# Transient refusals worth waiting out: rate limits, and the in-flight
# budget OpenRouter reserves per concurrent request. A 402 for genuinely
# exhausted credits is not retried.
MAX_RETRIES = 5

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def chat(model: str, messages: list, temperature: float, max_tokens: int,
         provider: dict | None = None, tools: list | None = None,
         tool_choice=None) -> dict:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise SystemExit(
            "Set OPENROUTER_API_KEY in .env (see .env.example) before running the eval."
        )

    body = {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if provider:
        body["provider"] = provider
    if tools:
        body["tools"] = tools
    if tool_choice is not None:
        body["tool_choice"] = tool_choice

    # Slow hosts reasoning toward MAX_TOKENS need minutes, not seconds.
    for attempt in range(MAX_RETRIES + 1):
        resp = requests.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=300,
        )
        if attempt < MAX_RETRIES and _transient(resp):
            time.sleep(float(resp.headers.get("Retry-After") or 2 ** attempt))
            continue
        break
    if resp.status_code != 200:
        raise RuntimeError(f"OpenRouter {resp.status_code}: {resp.text[:500]}")

    data = resp.json()
    if "choices" not in data:
        raise RuntimeError(f"OpenRouter returned no choices: {str(data)[:500]}")
    choice = data["choices"][0]
    usage = data.get("usage") or {}
    return {
        "message": choice["message"],
        "finish_reason": choice.get("finish_reason"),
        "input_tokens": int(usage.get("prompt_tokens") or 0),
        "output_tokens": int(usage.get("completion_tokens") or 0),
    }


def _transient(resp) -> bool:
    if resp.status_code == 429:
        return True
    if resp.status_code == 402:
        try:
            reason = resp.json()["error"]["metadata"]["reason"]
        except (ValueError, KeyError, TypeError):
            return False
        return reason == "in_flight_budget_exhausted"
    return False


def complete(model: str, prompt: str, temperature: float, max_tokens: int,
             provider: dict | None = None) -> dict:
    """Single-turn: what a model does with one shot at an ambiguous
    question, not how well it can interrogate the schema."""
    reply = chat(model, [{"role": "user", "content": prompt}],
                 temperature, max_tokens, provider=provider)
    return {
        "text": reply["message"].get("content") or "",
        "input_tokens": reply["input_tokens"],
        "output_tokens": reply["output_tokens"],
    }
