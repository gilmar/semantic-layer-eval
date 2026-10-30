"""Agentic mode: the strongest case for SQL.

The agent may probe DISTINCT values, test a query, read an error, and
retry. SQL arms get run_sql, the semantic arm gets cube_load, all get
answer. After MAX_TOOL_CALLS tool calls the next turn forces answer.
Tool errors go back to the agent as text (it may recover); only the
final answer is resolved and scored.

Tokens are summed over every turn, so footprint and cost reflect the
whole episode, including any catalog the SQL arms re-ship each turn.
"""

import json

import providers
import v2_arms
import v2_config as cfg
from v2_arms import LoudError

ANSWER_TOOL = {
    "type": "function",
    "function": {
        "name": "answer",
        "description": "Submit the final answer and end the session.",
        "parameters": {
            "type": "object",
            "properties": {
                "value": {
                    "description": ("A single number, or for a breakdown an object "
                                    "mapping each group label to its number."),
                    "anyOf": [
                        {"type": "number"},
                        {"type": "object", "additionalProperties": {"type": "number"}},
                    ],
                },
            },
            "required": ["value"],
        },
    },
}

RUN_SQL_TOOL = {
    "type": "function",
    "function": {
        "name": "run_sql",
        "description": ("Run one DuckDB SQL query against the warehouse. Returns the "
                        "column names, up to the row limit, and the total row count, "
                        "or the error message."),
        "parameters": {
            "type": "object",
            "properties": {"sql": {"type": "string"}},
            "required": ["sql"],
        },
    },
}

CUBE_LOAD_TOOL = {
    "type": "function",
    "function": {
        "name": "cube_load",
        "description": ("Run one Cube query against the semantic layer. Returns the "
                        "result rows, up to the row limit, or the error message."),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "object",
                    "description": "Cube query with measures, dimensions, filters, timeDimensions.",
                },
            },
            "required": ["query"],
        },
    },
}

FORCE_ANSWER = {"type": "function", "function": {"name": "answer"}}


def _tool_result(columns, rows, limit) -> str:
    return json.dumps({
        "columns": columns,
        "rows": [list(r) for r in rows[:limit]],
        "row_count": len(rows),
        "truncated": len(rows) > limit,
    }, default=str)


def _run_tool(name: str, args: dict, arm, con, meta) -> str:
    try:
        if name == "run_sql" and arm.engine == "sql":
            columns, rows = v2_arms.run_sql(con, v2_arms._strip_fences(str(args.get("sql", ""))))
            return _tool_result(columns, rows, cfg.TOOL_ROW_LIMIT)
        if name == "cube_load" and arm.engine == "cube":
            # Some models pass the query fields directly instead of under "query".
            raw = args.get("query", args if "measures" in args else None)
            query = v2_arms.validate_cube_query(v2_arms.parse_cube_query(raw), meta)
            rows = v2_arms.cube_load(query, cfg.CUBE_URL, cfg.CUBE_TOKEN)
            columns = list(rows[0].keys()) if rows else []
            return _tool_result(columns, [[r.get(c) for c in columns] for r in rows],
                                cfg.TOOL_ROW_LIMIT)
        return json.dumps({"error": f"Unknown tool {name!r} for this session"})
    except LoudError as exc:
        return json.dumps({"error": str(exc)})


def run_episode(arm, question: str, kind: str, expected_keys, model: str,
                meta: dict | None) -> dict:
    """Returns value (or None), error, error_kind ("loud" | "api" | ""),
    tool_calls, input/output tokens, and the full message transcript."""
    tools = [RUN_SQL_TOOL if arm.engine == "sql" else CUBE_LOAD_TOOL, ANSWER_TOOL]
    messages = [{"role": "user", "content": v2_arms.agentic_prompt(
        arm, question, meta, cfg.MAX_TOOL_CALLS, cfg.TOOL_ROW_LIMIT)}]
    state = {"value": None, "error": "", "error_kind": "", "tool_calls": 0,
             "input_tokens": 0, "output_tokens": 0, "transcript": messages,
             "answer_raw": ""}
    con = arm.connect() if arm.engine == "sql" else None
    stopped = False  # the model ended a turn without any tool call
    try:
        while True:
            # Forced answer after the tool budget, or after a turn that ended
            # without a tool call (some hosts drop the final tool call of a
            # reasoning model; forcing it recovers the answer the model reached).
            forced = state["tool_calls"] >= cfg.MAX_TOOL_CALLS or stopped
            reply = providers.chat(
                model, messages, cfg.TEMPERATURE, cfg.MAX_TOKENS,
                provider=cfg.PROVIDER_ROUTING.get(model), tools=tools,
                tool_choice=FORCE_ANSWER if forced else "auto")
            state["input_tokens"] += reply["input_tokens"]
            state["output_tokens"] += reply["output_tokens"]
            msg = reply["message"]
            calls = msg.get("tool_calls") or []
            turn = {"role": "assistant", "content": msg.get("content") or ""}
            if calls:
                turn["tool_calls"] = calls
            # Reasoning models lose the thread between tool calls unless their
            # reasoning is passed back (OpenRouter reasoning_details).
            if msg.get("reasoning_details"):
                turn["reasoning_details"] = msg["reasoning_details"]
            messages.append(turn)

            if not calls:
                if forced:
                    raise LoudError("Episode ended without an answer call")
                stopped = True
                messages.append({"role": "user",
                                 "content": "Call the answer tool with your final value."})
                continue

            for call in calls:
                name = call.get("function", {}).get("name")
                raw_args = call.get("function", {}).get("arguments") or "{}"
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except json.JSONDecodeError:
                    args = None
                if name == "answer":
                    state["answer_raw"] = raw_args if isinstance(raw_args, str) else json.dumps(raw_args)
                    if not isinstance(args, dict) or "value" not in args:
                        raise LoudError(f"Malformed answer arguments: {raw_args!r}")
                    state["value"] = v2_arms.resolve_answer(args["value"], kind, expected_keys)
                    return state
                state["tool_calls"] += 1
                result = (_run_tool(name, args, arm, con, meta) if isinstance(args, dict)
                          else json.dumps({"error": f"Arguments are not valid JSON: {raw_args!r}"}))
                messages.append({"role": "tool", "tool_call_id": call.get("id", ""),
                                 "content": result})
            if forced:
                raise LoudError("Forced answer turn did not call answer")
    except LoudError as exc:
        state["error"], state["error_kind"] = str(exc), "loud"
    except (RuntimeError, OSError) as exc:  # OpenRouter or network failure
        state["error"], state["error_kind"] = f"{type(exc).__name__}: {exc}", "api"
    finally:
        if con is not None:
            con.close()
    return state
