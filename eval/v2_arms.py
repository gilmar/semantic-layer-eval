"""Steelman v2 arms: registry, prompts, and answer resolution.

Four arms, one variable each (docs/steelman-v2-spec.md):

  raw_sql           bare tables, profiling notes, sample rows
  documented_prose  comments + full metrics dictionary, no views
  documented_views  comments + dictionary + view DDL (v1's documented_sql)
  semantic_layer    live Cube /meta: measures, dimensions, time dimensions

Every resolved answer is a number or a {group: number} breakdown. A
failure that the harness can see (SQL or Cube error, unknown member,
wrong result shape, unparseable reply) raises LoudError; a number that
resolves but is wrong is a silent error, scored by the runner.
"""

import json
import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable

import duckdb
import requests

import arms as v1

JSON_SHAPE = """{"measures": ["<measure>", ...],
 "dimensions": ["<dimension>", ...],
 "filters": [{"member": "<member>", "operator": "equals", "values": ["<value>", ...]}],
 "timeDimensions": [{"dimension": "<time dimension>", "dateRange": ["YYYY-MM-DD", "YYYY-MM-DD"], "granularity": "day|week|month"}]}"""

CUBE_SEMANTICS = """Omit keys you do not need. Date ranges are inclusive whole days. Granularity
buckets the result into calendar days, ISO weeks (Monday start), or calendar
months; without it the whole range is one result. Filter operators: equals,
notEquals, gt, gte, lt, lte, set, notSet, contains."""

BREAKDOWN_SQL = ("If the question asks for a breakdown, return one row per group: the group "
                 "label in the first column and the number in the second.")

QUERY_KEYS = {"measures", "dimensions", "filters", "timeDimensions", "segments",
              "order", "limit"}
GRANULARITIES = {"day", "week", "month", "quarter", "year"}


class LoudError(Exception):
    """A failure the harness can see: query error, schema violation,
    wrong result shape, or an unparseable reply."""


# ----------------------------------------------------------------- registry

@dataclass(frozen=True)
class Arm:
    name: str
    engine: str                       # "sql", "cube", or "csv"
    connect: Callable | None          # DuckDB environment for SQL arms
    render: Callable[[], str] | None  # catalog context (SQL) or documentation (csv)


def csv_documentation() -> str:
    """What a careful analyst pastes next to the spreadsheet: the users
    table and column notes and the full metrics dictionary, rendered from
    the same catalog the documented arms read (no views, no source
    objects, since the spreadsheet is all the model has)."""
    con = v1._connect_modeled(views=False)
    try:
        table_comment = con.execute(
            "SELECT comment FROM duckdb_tables() WHERE table_name = 'users'").fetchone()[0]
        columns = v1._render_columns(con, "users", with_comments=True)
        rows = con.execute(
            "SELECT metric_name, owner, definition FROM metrics_dictionary "
            "ORDER BY metric_name").fetchall()
    finally:
        con.close()
    dictionary = "\n".join(f"  {name} (owner: {owner})\n    definition: {definition}"
                           for name, owner, definition in rows)
    return (f"Column notes for users.csv:\n  {table_comment}\n{columns}\n\n"
            f"Official metric definitions (full metrics dictionary):\n{dictionary}")


ARMS = {
    "raw_sql": Arm("raw_sql", "sql", v1._connect_raw, v1.render_raw_schema),
    "documented_prose": Arm(
        "documented_prose", "sql",
        lambda: v1._connect_modeled(views=False),
        lambda: v1.render_modeled_docs(views=False)),
    "documented_views": Arm(
        "documented_views", "sql", v1._connect_modeled, v1.render_modeled_docs),
    "semantic_layer": Arm("semantic_layer", "cube", None, None),
    "csv_raw": Arm("csv_raw", "csv", None, None),
    "csv_documented": Arm("csv_documented", "csv", None, csv_documentation),
}

_context_cache: dict = {}


def sql_context(arm: Arm) -> str:
    """Catalog, profiling notes, and samples; identical across modes and
    questions, so rendered once per arm."""
    if arm.name not in _context_cache:
        _context_cache[arm.name] = f"""{arm.render()}

{v1.PROFILING_NOTES}

Sample rows from users:
{v1._sample_rows(v1.CSV_PATH)}

Sample rows from events:
{v1._sample_rows(v1.EVENTS_CSV_PATH)}"""
    return _context_cache[arm.name]


# ------------------------------------------------------------------- Cube

def fetch_cube_meta(cube_url: str, token: str) -> dict:
    """Live measures, dimensions, and time dimensions from Cube /meta."""
    r = requests.get(f"{cube_url}/cubejs-api/v1/meta",
                     headers={"Authorization": token}, timeout=15)
    r.raise_for_status()
    meta = {"measures": [], "dimensions": [], "time_dimensions": []}
    for cube in r.json().get("cubes", []):
        for m in cube.get("measures", []):
            meta["measures"].append({
                "name": m["name"], "title": m.get("title", ""),
                "description": m.get("description", "") or "",
            })
        for d in cube.get("dimensions", []):
            entry = {"name": d["name"], "type": d.get("type", ""),
                     "description": d.get("description", "") or ""}
            key = "time_dimensions" if d.get("type") == "time" else "dimensions"
            meta[key].append(entry)
    if not meta["measures"]:
        raise RuntimeError("Cube /meta returned no measures. Is the model mounted?")
    return meta


def cube_context(meta: dict) -> str:
    measures = "\n".join(
        f"- {m['name']}\n    title: {m['title']}\n    description: {m['description'].strip()}"
        for m in meta["measures"])
    dims = "\n".join(
        f"- {d['name']} ({d['type']})" + (f": {d['description'].strip()}" if d["description"] else "")
        for d in meta["dimensions"])
    tdims = "\n".join(
        f"- {d['name']}" + (f": {d['description'].strip()}" if d["description"] else "")
        for d in meta["time_dimensions"])
    return f"""Available measures:
{measures}

Available dimensions (group by or filter on these):
{dims}

Available time dimensions (date ranges and granularity):
{tdims}"""


def validate_cube_query(query, meta: dict) -> dict:
    """Reject unknown members before Cube sees the query: a schema
    violation is a loud error, not a retry."""
    if not isinstance(query, dict):
        raise LoudError(f"Cube query must be a JSON object, got {type(query).__name__}")
    extra = set(query) - QUERY_KEYS
    if extra:
        raise LoudError(f"Unknown Cube query keys: {sorted(extra)}")
    measures = {m["name"] for m in meta["measures"]}
    dims = {d["name"] for d in meta["dimensions"]}
    tdims = {d["name"] for d in meta["time_dimensions"]}
    if not query.get("measures"):
        raise LoudError("Cube query has no measures")
    for m in query["measures"]:
        if m not in measures:
            raise LoudError(f"Unknown measure: {m}")
    for d in query.get("dimensions", []):
        if d not in dims | tdims:
            raise LoudError(f"Unknown dimension: {d}")
    for f in query.get("filters", []):
        if not isinstance(f, dict) or f.get("member", f.get("dimension")) not in measures | dims | tdims:
            raise LoudError(f"Unknown filter member: {f}")
    for td in query.get("timeDimensions", []):
        if not isinstance(td, dict) or td.get("dimension") not in tdims:
            raise LoudError(f"Unknown time dimension: {td}")
        if td.get("granularity") and td["granularity"] not in GRANULARITIES:
            raise LoudError(f"Unknown granularity: {td['granularity']}")
    return query


def cube_load(query: dict, cube_url: str, token: str) -> list:
    for _ in range(30):  # Cube replies 'Continue wait' while warming up
        r = requests.post(f"{cube_url}/cubejs-api/v1/load", json={"query": query},
                          headers={"Authorization": token}, timeout=60)
        body = r.json()
        if body.get("error") == "Continue wait":
            time.sleep(1)
            continue
        if "error" in body:
            raise LoudError(f"Cube error: {body['error']}")
        return body["data"]
    raise RuntimeError("Cube kept replying 'Continue wait'")


# ------------------------------------------------------------- prompts

CSV_REPLY = ("Reply with a single number and nothing else. "
             "Give percentages with two decimal places.")


def csv_prompt(arm: Arm, question: str) -> str:
    """csv_raw is v1's spreadsheet prompt plus a precision instruction
    (shares are scored within 0.02pp, so a correctly rounded "57.9" would
    otherwise score as wrong); csv_documented adds the documentation a
    careful analyst would paste alongside the spreadsheet."""
    data = v1.CSV_PATH.read_text()
    if arm.render is None:
        return f"""Below is the full contents of users.csv, our user spreadsheet.

{data}

Question: {question}

{CSV_REPLY}"""
    return f"""Below is the full contents of users.csv, our user spreadsheet, followed by the column notes and official metric definitions from our data team.

{data}

{arm.render()}

Question: {question}

{CSV_REPLY}"""


def single_shot_prompt(arm: Arm, question: str, meta: dict | None) -> str:
    if arm.engine == "csv":
        return csv_prompt(arm, question)
    if arm.engine == "sql":
        return f"""You are a data analyst with direct SQL access to the production database (DuckDB dialect).

{sql_context(arm)}

Question: {question}

Reply with exactly one SQL query and nothing else. No explanation, no code fences.
{BREAKDOWN_SQL}"""
    return f"""You are connected to the company's governed semantic layer (Cube).

{cube_context(meta)}

Question: {question}

Reply with ONLY one JSON Cube query, no code fences and no explanation, with this shape:
{JSON_SHAPE}
{CUBE_SEMANTICS}"""


def agentic_prompt(arm: Arm, question: str, meta: dict | None,
                   max_tool_calls: int, row_limit: int) -> str:
    answer = ("When you are confident, call answer with the final value: a single number, "
              "or for a breakdown an object mapping each group label to its number.")
    if arm.engine == "sql":
        return f"""You are a data analyst with access to the production database (DuckDB dialect) through the run_sql tool.

{sql_context(arm)}

Question: {question}

Use run_sql to explore and check your work as needed: up to {max_tool_calls} queries, each returning at most {row_limit} rows. {answer}"""
    return f"""You are connected to the company's governed semantic layer (Cube) through the cube_load tool.

{cube_context(meta)}

cube_load takes one Cube query object with this shape:
{JSON_SHAPE}
{CUBE_SEMANTICS}

Question: {question}

Use cube_load as needed: up to {max_tool_calls} queries, each returning at most {row_limit} rows. {answer}"""


# ---------------------------------------------------------- normalization

MONTH_NAMES = {
    "jan": 1, "feb": 2, "fev": 2, "mar": 3, "apr": 4, "abr": 4, "may": 5, "mai": 5,
    "jun": 6, "jul": 7, "aug": 8, "ago": 8, "sep": 9, "set": 9, "oct": 10, "out": 10,
    "nov": 11, "dec": 12, "dez": 12,
}


def _month_key(raw, year: int) -> str:
    if isinstance(raw, (datetime, date)):
        return f"{raw.year:04d}-{raw.month:02d}"
    if isinstance(raw, (int, float)) and 1 <= raw <= 12 and float(raw).is_integer():
        return f"{year:04d}-{int(raw):02d}"
    text = str(raw).strip().lower()
    m = re.search(r"(\d{4})[-_/](\d{1,2})", text)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}"
    if text.isdigit() and 1 <= int(text) <= 12:
        return f"{year:04d}-{int(text):02d}"
    for name, number in MONTH_NAMES.items():
        if text.startswith(name):
            y = re.search(r"\d{4}", text)
            return f"{int(y.group()) if y else year:04d}-{number:02d}"
    return text


def normalize_key(raw, expected_keys) -> str:
    """Map a group label to the ground truth's key space: YYYY-MM months
    when the expected keys are months, lowercase labels otherwise."""
    keys = list(expected_keys or [])
    if keys and all(re.fullmatch(r"\d{4}-\d{2}", k) for k in keys):
        return _month_key(raw, int(keys[0][:4]))
    return str(raw).strip().lower()


def to_number(value) -> float:
    if isinstance(value, bool) or value is None:
        raise LoudError(f"Expected a number, got {value!r}")
    try:
        return float(value)
    except (TypeError, ValueError):
        raise LoudError(f"Expected a number, got {value!r}") from None


def breakdown(pairs, expected_keys) -> dict:
    out = {}
    for label, number in pairs:
        key = normalize_key(label, expected_keys)
        if key in out:
            raise LoudError(f"Duplicate group {key!r} in breakdown")
        out[key] = to_number(number)
    return out


def _strip_fences(text: str) -> str:
    return v1._strip_fences(text)


# ------------------------------------------------------------ resolution

def shape_sql_result(columns: list, rows: list, kind: str, expected_keys):
    if kind != "vector":
        if len(rows) != 1:
            raise LoudError(f"Expected one row, got {len(rows)}")
        return to_number(rows[0][0])
    if len(rows) == 1 and len(columns) == 1:
        return to_number(rows[0][0])
    if len(columns) == 2:
        return breakdown(((r[0], r[1]) for r in rows), expected_keys)
    if len(rows) == 1 and len(columns) > 2:
        return breakdown(zip(columns, rows[0]), expected_keys)
    raise LoudError(f"Unexpected result shape: {len(rows)} rows x {len(columns)} columns")


def run_sql(con, sql: str):
    try:
        cur = con.execute(sql)
        return [d[0] for d in cur.description], cur.fetchall()
    except duckdb.Error as exc:
        raise LoudError(f"SQL error: {exc}") from None


def shape_cube_result(query: dict, rows: list, kind: str, expected_keys):
    measures = query["measures"]
    if len(measures) != 1:
        raise LoudError(f"Query has {len(measures)} measures; the question needs one number "
                        "per group, and Cube does not combine measures")
    measure = measures[0]
    groups = list(query.get("dimensions", [])) + [
        f"{td['dimension']}.{td['granularity']}"
        for td in query.get("timeDimensions", []) if td.get("granularity")]
    if kind != "vector" or not groups:
        if len(rows) != 1:
            raise LoudError(f"Expected one row, got {len(rows)}")
        return to_number(rows[0].get(measure))
    if len(groups) > 1:
        raise LoudError(f"Breakdown grouped by {len(groups)} keys: {groups}")
    return breakdown(((r.get(groups[0]), r.get(measure)) for r in rows), expected_keys)


def parse_cube_query(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(_strip_fences(str(raw)))
    except json.JSONDecodeError as exc:
        raise LoudError(f"Reply is not a JSON Cube query: {exc}") from None


def resolve_single_shot(arm: Arm, reply: str, kind: str, expected_keys,
                        meta: dict | None, cube_url: str, token: str):
    if arm.engine == "csv":
        try:
            return to_number(v1._first_number(reply))
        except ValueError as exc:
            raise LoudError(str(exc)) from None
    if arm.engine == "sql":
        con = arm.connect()
        try:
            columns, rows = run_sql(con, _strip_fences(reply))
        finally:
            con.close()
        return shape_sql_result(columns, rows, kind, expected_keys)
    query = validate_cube_query(parse_cube_query(reply), meta)
    return shape_cube_result(query, cube_load(query, cube_url, token), kind, expected_keys)


def resolve_answer(value, kind: str, expected_keys):
    """The agent's answer(value): a number, or a {group: number} object."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            raise LoudError(f"Answer is not a number or object: {value!r}") from None
    if isinstance(value, dict):
        if kind != "vector":
            raise LoudError("Answer is a breakdown; the question asks for one number")
        return breakdown(value.items(), expected_keys)
    return to_number(value)
