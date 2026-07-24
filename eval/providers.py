"""Model providers. One function: complete(model, prompt) -> reply dict.

The reply must carry token usage so the harness can price every call:

    {"text": str, "input_tokens": int, "output_tokens": int}

Anthropic is wired in. To add another provider, branch on the model
name prefix and return the same shape; if a provider does not report
usage, return zeros and expect the cost columns to be wrong for it.
Keep it single-turn: the point of the experiment is what a model does
with one shot at an ambiguous question, not how well it can
interrogate the schema over ten turns.
"""

import os


def complete(model: str, prompt: str, temperature: float, max_tokens: int) -> dict:
    if model.startswith("claude"):
        return _anthropic(model, prompt, temperature, max_tokens)
    # elif model.startswith("gpt"):
    #     return _openai(model, prompt, temperature, max_tokens)
    raise ValueError(
        f"No provider wired for model '{model}'. Add a branch in providers.py."
    )


def _anthropic(model: str, prompt: str, temperature: float, max_tokens: int) -> dict:
    import anthropic

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("Set ANTHROPIC_API_KEY before running the eval.")

    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        messages=[{"role": "user", "content": prompt}],
    )
    return {
        "text": "".join(b.text for b in msg.content if b.type == "text"),
        "input_tokens": msg.usage.input_tokens,
        "output_tokens": msg.usage.output_tokens,
    }
