"""Single source of truth for the two database versions.

Two builds of the same 300 rows:

  build_raw(con)      the undocumented version. One bare table, correct
                      types, no comments, no dictionary, no views. This
                      is what "connect the agent to the database" usually
                      means in practice.

  build_modeled(con)  the steelman of a traditional warehouse. Same table
                      plus: table and column comments stored in the
                      catalog, a metrics_dictionary table that fully
                      documents every official metric in prose, and a
                      curated view (vw_active_users) that encodes the
                      governed numerator. Nothing is hidden. If
                      documentation could substitute for a semantic
                      layer, this version is where it would happen.

Both db/load_duckdb.py (the file Cube serves) and eval/arms.py (the
in-memory environments the harness queries) call these same functions,
so the documentation a model sees in its prompt is rendered from the
same catalog a human would inspect.

Comment text avoids apostrophes on purpose: COMMENT ON takes a SQL
string literal.
"""

from pathlib import Path

CSV_COLUMNS = (
    "{'user_id': 'INTEGER', 'email': 'VARCHAR', 'full_name': 'VARCHAR', "
    "'status': 'VARCHAR', 'is_deleted': 'BOOLEAN', 'is_test_account': 'BOOLEAN', "
    "'plan': 'VARCHAR', 'created_at': 'DATE', 'last_login_at': 'DATE'}"
)

EVENTS_CSV_COLUMNS = (
    "{'event_id': 'INTEGER', 'user_id': 'INTEGER', "
    "'event_type': 'VARCHAR', 'occurred_at': 'TIMESTAMP'}"
)

TABLE_COMMENT = (
    "Registered users of the product. Includes soft-deleted rows and internal "
    "QA test accounts. Both must be excluded from all business metrics."
)

COLUMN_COMMENTS = {
    "user_id": "Primary key.",
    "email": "Login email. QA accounts use the qa.example.com domain.",
    "full_name": "Display name.",
    "status": (
        "Lifecycle status: active, inactive, pending, churned. The value active "
        "means the account is enabled. It does NOT mean the user logged in recently."
    ),
    "is_deleted": (
        "Soft-delete flag. Deleted rows are retained for audit and MUST be "
        "excluded from all business metrics."
    ),
    "is_test_account": (
        "Internal QA account. MUST be excluded from all business metrics."
    ),
    "plan": (
        "Billing plan: free, pro, enterprise. Billing status is independent of "
        "activity status."
    ),
    "created_at": "Signup date.",
    "last_login_at": (
        "Date of most recent login. NULL if the user never logged in. Login "
        "recency is not part of any official activity definition."
    ),
}

VIEW_ACTIVE_COMMENT = (
    "Curated view. One row per active user under the official definition: "
    "status is active, not soft-deleted, not a test account. Counting this "
    "view yields the official Active Users metric."
)

EVENTS_TABLE_COMMENT = (
    "Product and system events, one row per event. A user has many events, "
    "so joining users to events multiplies rows."
)

EVENTS_COLUMN_COMMENTS = {
    "event_id": "Primary key.",
    "user_id": "References users.user_id. A user has many events.",
    "event_type": (
        "One of: login, card_created, card_moved, report_viewed, api_call, "
        "email_opened, webhook_ping. email_opened is passive and webhook_ping "
        "is machine generated; official activity metrics count only the "
        "other five."
    ),
    "occurred_at": "Event timestamp, including time of day.",
}

VIEW_QUALIFYING_COMMENT = (
    "Curated fact view. Qualifying product events (passive and machine "
    "events removed) by valid users (soft-deleted and test accounts "
    "removed). One row per event. The count of DISTINCT user_id over a "
    "calendar month yields the official Monthly Active Users metric."
)

# (metric_name, owner, definition, source_object)
METRICS_DICTIONARY = [
    (
        "active_users",
        "Growth Analytics",
        "Count of users with status = active, excluding soft-deleted rows and "
        "test accounts. This is the canonical definition of an active user for "
        "all company reporting.",
        "vw_active_users",
    ),
    (
        "active_share",
        "Growth Analytics",
        "Percentage of the reporting universe that is active. Numerator: "
        "active_users. Denominator: all users excluding soft-deleted rows and "
        "test accounts. Expressed as a percentage.",
        "",
    ),
    (
        "monthly_active_users",
        "Growth Analytics",
        "Count of distinct valid users (excluding soft-deleted rows and test "
        "accounts) with at least one qualifying product event in the calendar "
        "month. Qualifying events exclude email_opened and webhook_ping.",
        "vw_qualifying_events",
    ),
    (
        "churned_users",
        "Growth Analytics",
        "Count of users with status = churned, excluding soft-deleted rows and "
        "test accounts.",
        "",
    ),
    (
        "paying_users",
        "Finance",
        "Count of users on the pro or enterprise plan, excluding soft-deleted "
        "rows and test accounts.",
        "",
    ),
]


def _read_csv_expr(csv_path: Path) -> str:
    return (
        f"read_csv('{csv_path.as_posix()}', header=true, columns={CSV_COLUMNS})"
    )


def _prefix(schema: str | None) -> str:
    return f"{schema}." if schema else ""


def _events_csv_expr(events_csv_path: Path) -> str:
    return (
        f"read_csv('{events_csv_path.as_posix()}', header=true, "
        f"columns={EVENTS_CSV_COLUMNS})"
    )


def build_raw(con, csv_path: Path, events_csv_path: Path,
              schema: str | None = None) -> None:
    """The undocumented version: two bare tables, nothing else."""
    p = _prefix(schema)
    if schema:
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    con.execute(
        f"CREATE TABLE {p}users AS SELECT * FROM {_read_csv_expr(csv_path)}"
    )
    con.execute(
        f"CREATE TABLE {p}events AS SELECT * FROM {_events_csv_expr(events_csv_path)}"
    )


def build_modeled(con, csv_path: Path, events_csv_path: Path,
                  schema: str | None = None) -> None:
    """The documented version: comments, dictionary, curated views."""
    p = _prefix(schema)
    if schema:
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")

    con.execute(
        f"CREATE TABLE {p}users AS SELECT * FROM {_read_csv_expr(csv_path)}"
    )
    con.execute(f"COMMENT ON TABLE {p}users IS '{TABLE_COMMENT}'")
    for col, comment in COLUMN_COMMENTS.items():
        con.execute(f"COMMENT ON COLUMN {p}users.{col} IS '{comment}'")

    con.execute(
        f"CREATE TABLE {p}metrics_dictionary ("
        "metric_name VARCHAR, owner VARCHAR, definition VARCHAR, "
        "source_object VARCHAR)"
    )
    con.executemany(
        f"INSERT INTO {p}metrics_dictionary VALUES (?, ?, ?, ?)",
        METRICS_DICTIONARY,
    )
    con.execute(
        f"COMMENT ON TABLE {p}metrics_dictionary IS "
        "'Official metric definitions. Owned by the data team.'"
    )

    con.execute(
        f"CREATE TABLE {p}events AS SELECT * FROM {_events_csv_expr(events_csv_path)}"
    )
    con.execute(f"COMMENT ON TABLE {p}events IS '{EVENTS_TABLE_COMMENT}'")
    for col, comment in EVENTS_COLUMN_COMMENTS.items():
        con.execute(f"COMMENT ON COLUMN {p}events.{col} IS '{comment}'")

    con.execute(
        f"CREATE VIEW {p}vw_active_users AS "
        f"SELECT * FROM {p}users "
        "WHERE status = 'active' AND is_deleted = false AND is_test_account = false"
    )
    con.execute(f"COMMENT ON VIEW {p}vw_active_users IS '{VIEW_ACTIVE_COMMENT}'")

    con.execute(
        f"CREATE VIEW {p}vw_qualifying_events AS "
        f"SELECT e.event_id, e.user_id, e.event_type, e.occurred_at "
        f"FROM {p}events e "
        f"JOIN {p}users u ON u.user_id = e.user_id "
        "WHERE u.is_deleted = false AND u.is_test_account = false "
        "AND e.event_type NOT IN ('email_opened', 'webhook_ping')"
    )
    con.execute(
        f"COMMENT ON VIEW {p}vw_qualifying_events IS '{VIEW_QUALIFYING_COMMENT}'"
    )
