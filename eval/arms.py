"""Experimental arms.

The unit of comparison is the resolved number. The datasets guarantee
distinct values across plausible interpretations (counts are distinct
integers; share ratios are separated by at least 0.06 percentage
points), so each answer identifies the definition the model silently
chose.

The two SQL arms differ in exactly one variable: documentation.

  raw_sql         the undocumented version. Two bare tables, profiling
                  facts, sample rows. Nothing else exists.
  documented_sql  the modeled version. The prompt is RENDERED LIVE from
                  the database catalog: table and column comments,
                  metrics_dictionary contents, curated view DDL. Perfect
                  retrieval is granted on purpose; it biases the test
                  against the semantic-layer thesis, so any surviving
                  gap is structural rather than a documentation-quality
                  artifact.

The semantic arm speaks the real protocol: the model emits a small JSON
Cube query (one measure, optionally a time dimension date range), the
harness sends it to Cube, Cube executes. This is how agents talk to a
semantic layer over MCP in practice, and it is what makes the June
question answerable through the layer.
"""

import csv
import json
import re
import sys
import time
from pathlib import Path

import duckdb
import requests

ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "data" / "users.csv"
EVENTS_CSV_PATH = ROOT / "data" / "events.csv"

sys.path.insert(0, str(ROOT / "db"))
import schema  # noqa: E402


# ------------------------------------------------------------ environments

def _connect_raw():
    con = duckdb.connect()
    schema.build_raw(con, CSV_PATH, EVENTS_CSV_PATH)
    return con


def _connect_modeled():
    con = duckdb.connect()
    schema.build_modeled(con, CSV_PATH, EVENTS_CSV_PATH)
    return con


# --------------------------------------------------------- shared helpers

def _sample_rows(path: Path, n: int = 5) -> str:
    with open(path, newline="") as f:
        reader = csv.reader(f)
        lines = [",".join(row) for _, row in zip(range(n + 1), reader)]
    return "\n".join(lines)


PROFILING_NOTES = """Profiling notes (obtained by running SELECT DISTINCT and min/max):
  users.status: active, churned, inactive, pending
  users.plan: enterprise, free, pro
  users.last_login_at contains NULLs
  events.event_type: api_call, card_created, card_moved, email_opened, login, report_viewed, webhook_ping
  events.occurred_at ranges from 2023 to 2026-07-01 and includes time of day"""


def _first_number(text: str) -> float:
    m = re.search(r"-?\d[\d,]*\.?\d*", text.replace("**", ""))
    if not m:
        raise ValueError(f"No number found in response: {text[:200]!r}")
    return float(m.group().replace(",", ""))


def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


# ------------------------------------------------- live catalog rendering

def _render_columns(con, table: str, with_comments: bool) -> str:
    rows = con.execute(
        "SELECT column_name, data_type, comment FROM duckdb_columns() "
        "WHERE table_name = ? ORDER BY column_index",
        [table],
    ).fetchall()
    out = []
    for name, dtype, comment in rows:
        line = f"  {name:17s}{dtype}"
        if with_comments and comment:
            line += f"   -- {comment}"
        out.append(line)
    return "\n".join(out)


def render_raw_schema() -> str:
    con = _connect_raw()
    try:
        users_cols = _render_columns(con, "users", with_comments=False)
        events_cols = _render_columns(con, "events", with_comments=False)
    finally:
        con.close()
    return f"""TABLE users
{users_cols}

TABLE events
{events_cols}

No table comments, column comments, documentation tables, or curated views exist in this database."""


def render_modeled_docs() -> str:
    """Everything the model sees comes out of the live catalog, so the
    documentation shown is provably the documentation stored."""
    con = _connect_modeled()
    try:
        tables = []
        for table in ("users", "events"):
            comment = con.execute(
                "SELECT comment FROM duckdb_tables() WHERE table_name = ?",
                [table],
            ).fetchone()[0]
            cols = _render_columns(con, table, with_comments=True)
            tables.append(f"TABLE {table}\n  comment: {comment}\n{cols}")

        views = []
        for name, comment, sql in con.execute(
            "SELECT view_name, comment, sql FROM duckdb_views() "
            "WHERE NOT internal ORDER BY view_name"
        ).fetchall():
            views.append(f"VIEW {name}\n  comment: {comment}\n  definition: {sql}")

        dict_rows = con.execute(
            "SELECT metric_name, owner, definition, source_object "
            "FROM metrics_dictionary ORDER BY metric_name"
        ).fetchall()
    finally:
        con.close()

    dictionary = "\n".join(
        f"  {name} (owner: {owner})\n    definition: {definition}"
        + (f"\n    curated object: {src}" if src else "")
        for name, owner, definition, src in dict_rows
    )

    parts = ["""This warehouse is well modelled and documented. Everything below is rendered
live from the database catalog (comments, dictionary rows, and view DDL are
stored in the warehouse itself)."""]
    parts.extend(tables)
    parts.extend(views)
    parts.append(
        f"TABLE metrics_dictionary (official metric definitions, full contents):\n{dictionary}"
    )
    return "\n\n".join(parts)


# ------------------------------------------------------------- SQL arms

_SQL_INSTRUCTIONS = "Reply with exactly one SQL query and nothing else. No explanation, no code fences."


def build_raw_sql_prompt(question: str) -> str:
    return f"""You are a data analyst with direct SQL access to the production database (DuckDB dialect).

{render_raw_schema()}

{PROFILING_NOTES}

Sample rows from users:
{_sample_rows(CSV_PATH)}

Sample rows from events:
{_sample_rows(EVENTS_CSV_PATH)}

Question: {question}

{_SQL_INSTRUCTIONS}"""


def build_documented_sql_prompt(question: str) -> str:
    return f"""You are a data analyst with direct SQL access to the production database (DuckDB dialect).

{render_modeled_docs()}

{PROFILING_NOTES}

Sample rows from users:
{_sample_rows(CSV_PATH)}

Sample rows from events:
{_sample_rows(EVENTS_CSV_PATH)}

Question: {question}

{_SQL_INSTRUCTIONS}"""


def resolve_sql(model_output: str, modeled: bool) -> float:
    sql = _strip_fences(model_output)
    con = _connect_modeled() if modeled else _connect_raw()
    try:
        result = con.execute(sql).fetchall()
    finally:
        con.close()
    if not result or result[0][0] is None:
        raise ValueError("Query returned no value")
    return float(result[0][0])


# ---------------------------------------------------------- csv_in_context

def build_csv_prompt(question: str) -> str:
    return f"""Below is the full contents of users.csv, our user spreadsheet.

{CSV_PATH.read_text()}

Question: {question}

Reply with a single number and nothing else."""


def resolve_csv(model_output: str) -> float:
    return _first_number(model_output)


# ---------------------------------------------------------- semantic_layer

def fetch_cube_meta(cube_url: str, token: str) -> dict:
    """Pull live measure and time-dimension metadata from Cube. The agent
    sees what Cube actually exposes, not a hand-written copy of it."""
    r = requests.get(
        f"{cube_url}/cubejs-api/v1/meta",
        headers={"Authorization": token},
        timeout=15,
    )
    r.raise_for_status()
    measures, time_dimensions = [], []
    for cube in r.json().get("cubes", []):
        for m in cube.get("measures", []):
            measures.append({
                "name": m["name"],
                "title": m.get("title", ""),
                "description": m.get("description", "") or m.get("shortTitle", ""),
            })
        for d in cube.get("dimensions", []):
            if d.get("type") == "time":
                time_dimensions.append({
                    "name": d["name"],
                    "description": d.get("description", "") or d.get("title", ""),
                })
    if not measures:
        raise RuntimeError("Cube /meta returned no measures. Is the model mounted?")
    return {"measures": measures, "time_dimensions": time_dimensions}


def build_semantic_prompt(question: str, meta: dict) -> str:
    measures = "\n".join(
        f"- {m['name']}\n    title: {m['title']}\n    description: {m['description']}"
        for m in meta["measures"]
    )
    time_dims = "\n".join(
        f"- {d['name']}: {d['description']}" for d in meta["time_dimensions"]
    ) or "  (none)"
    return f"""You are connected to the company's governed semantic layer (Cube).

Available measures:
{measures}

Available time dimensions (for date filtering):
{time_dims}

Question: {question}

Reply with ONLY a JSON Cube query, no code fences and no explanation, in one of these shapes:
{{"measures": ["<one measure name>"]}}
or, when the question refers to a specific time period:
{{"measures": ["<one measure name>"], "timeDimensions": [{{"dimension": "<time dimension name>", "dateRange": ["YYYY-MM-DD", "YYYY-MM-DD"]}}]}}
Use exactly one measure. Do not include granularity."""


def resolve_semantic(model_output: str, cube_url: str, token: str) -> float:
    query = json.loads(_strip_fences(model_output))
    measures = query.get("measures", [])
    if len(measures) != 1:
        raise ValueError(f"Expected exactly one measure, got {measures}")
    measure = measures[0]

    for _ in range(20):  # Cube returns 'Continue wait' while warming up
        r = requests.post(
            f"{cube_url}/cubejs-api/v1/load",
            json={"query": query},
            headers={"Authorization": token},
            timeout=30,
        )
        body = r.json()
        if body.get("error") == "Continue wait":
            time.sleep(1)
            continue
        if "error" in body:
            raise RuntimeError(f"Cube error: {body['error']}")
        data = body["data"]
        if len(data) != 1:
            raise ValueError(f"Expected a single result row, got {len(data)}")
        return float(data[0][measure])
    raise TimeoutError("Cube kept replying 'Continue wait'")


# -------------------------------------------------------------- fingerprints

def compute_count_fingerprints() -> dict:
    """Every plausible reading of 'how many active users', computed live
    (login-based readings depend on CURRENT_DATE at eval time)."""
    con = _connect_raw()
    q = lambda sql: con.execute(sql).fetchone()[0]  # noqa: E731
    fp = {
        "total_rows": q("SELECT count(*) FROM users"),
        "status_active_only": q(
            "SELECT count(*) FROM users WHERE status = 'active'"),
        "status_active_excl_deleted": q(
            "SELECT count(*) FROM users WHERE status = 'active' AND NOT is_deleted"),
        "status_active_excl_test": q(
            "SELECT count(*) FROM users WHERE status = 'active' AND NOT is_test_account"),
        "governed_active_users": q(
            "SELECT count(*) FROM users WHERE status = 'active' "
            "AND NOT is_deleted AND NOT is_test_account"),
        "any_status_excl_deleted": q(
            "SELECT count(*) FROM users WHERE NOT is_deleted"),
        "valid_universe": q(
            "SELECT count(*) FROM users WHERE NOT is_deleted AND NOT is_test_account"),
        "active_or_pending_excl_deleted_test": q(
            "SELECT count(*) FROM users WHERE status IN ('active','pending') "
            "AND NOT is_deleted AND NOT is_test_account"),
        "not_churned_not_inactive": q(
            "SELECT count(*) FROM users WHERE status IN ('active','pending')"),
        "paying_plan_any_status": q(
            "SELECT count(*) FROM users WHERE plan != 'free'"),
        "paying_and_status_active": q(
            "SELECT count(*) FROM users WHERE plan != 'free' AND status = 'active'"),
        "login_within_30d": q(
            "SELECT count(*) FROM users WHERE last_login_at >= CURRENT_DATE - INTERVAL 30 DAY"),
        "login_within_90d": q(
            "SELECT count(*) FROM users WHERE last_login_at >= CURRENT_DATE - INTERVAL 90 DAY"),
        "active_and_login_30d": q(
            "SELECT count(*) FROM users WHERE status = 'active' "
            "AND last_login_at >= CURRENT_DATE - INTERVAL 30 DAY"),
    }
    con.close()
    return fp


def compute_share_fingerprints(count_fp: dict) -> dict:
    """Plausible numerator/denominator combinations for the share
    question, as percentages. The seed guarantees they are separated by
    more than 0.06 percentage points, so a 0.02pp matching tolerance
    is unambiguous."""
    numerators = {
        "governed_active": count_fp["governed_active_users"],
        "status_active_only": count_fp["status_active_only"],
        "active_excl_deleted": count_fp["status_active_excl_deleted"],
        "active_excl_test": count_fp["status_active_excl_test"],
    }
    denominators = {
        "total_rows": count_fp["total_rows"],
        "excl_deleted": count_fp["any_status_excl_deleted"],
        "valid_universe": count_fp["valid_universe"],
    }
    return {
        f"{nk} / {dk}": 100.0 * nv / dv
        for nk, nv in numerators.items()
        for dk, dv in denominators.items()
    }


_JUNE = ("TIMESTAMP '2026-06-01'", "TIMESTAMP '2026-07-01'")
_VALID_JOIN = (
    "FROM events e JOIN users u ON u.user_id = e.user_id "
    "WHERE NOT u.is_deleted AND NOT u.is_test_account "
)
_QUALIFYING = "AND e.event_type NOT IN ('email_opened','webhook_ping') "
_IN_JUNE = f"AND e.occurred_at >= {_JUNE[0]} AND e.occurred_at < {_JUNE[1]}"


def compute_mau_fingerprints(count_fp: dict) -> dict:
    """June 2026 MAU under every plausible interpretation, including the
    BETWEEN boundary bug and the two status-based confusions."""
    con = _connect_raw()
    q = lambda sql: con.execute(sql).fetchone()[0]  # noqa: E731
    fp = {
        "governed_mau": q(
            "SELECT count(DISTINCT e.user_id) "
            + _VALID_JOIN + _QUALIFYING + _IN_JUNE),
        "mau_incl_passive_events": q(
            "SELECT count(DISTINCT e.user_id) " + _VALID_JOIN + _IN_JUNE),
        "mau_incl_test_and_deleted": q(
            "SELECT count(DISTINCT user_id) FROM events e WHERE true "
            + _QUALIFYING + _IN_JUNE),
        "mau_all_users_all_events": q(
            "SELECT count(DISTINCT user_id) FROM events e WHERE true " + _IN_JUNE),
        "mau_between_boundary_bug": q(
            "SELECT count(DISTINCT e.user_id) " + _VALID_JOIN + _QUALIFYING
            + f"AND e.occurred_at BETWEEN {_JUNE[0]} AND TIMESTAMP '2026-06-30'"),
        "mau_requiring_status_active": q(
            "SELECT count(DISTINCT e.user_id) "
            + _VALID_JOIN + "AND u.status = 'active' " + _QUALIFYING + _IN_JUNE),
        "event_rows_qualifying_june_valid": q(
            "SELECT count(*) " + _VALID_JOIN + _QUALIFYING + _IN_JUNE),
        "event_rows_all_june": q(
            "SELECT count(*) FROM events e WHERE true " + _IN_JUNE),
        "confused_with_status_active_count": count_fp["status_active_only"],
        "confused_with_governed_active_users": count_fp["governed_active_users"],
    }
    con.close()
    return fp
