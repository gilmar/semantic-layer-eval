"""Load data/users.csv and data/events.csv into db/demo.duckdb, in TWO versions.

  schema raw      undocumented tables, exactly as they usually exist
  schema modeled  the same rows, fully documented: catalog comments,
                  metrics_dictionary, curated views (vw_active_users,
                  vw_qualifying_events)

Cube connects to the modeled schema (the semantic layer sits on top of
the well-modelled warehouse, it does not replace good modelling). Humans
can open the file and inspect both versions side by side.

The eval harness deliberately does NOT open this file; it rebuilds the
same two environments in memory from the CSVs via db/schema.py, so
there is never a file-lock conflict with Cube (DuckDB allows one
read-write process per database file).
"""

import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "db"))
import schema  # noqa: E402

CSV_PATH = ROOT / "data" / "users.csv"
EVENTS_CSV_PATH = ROOT / "data" / "events.csv"
DB_PATH = ROOT / "db" / "demo.duckdb"


def main():
    if not CSV_PATH.exists() or not EVENTS_CSV_PATH.exists():
        raise SystemExit("CSVs not found. Run: python data/generate_data.py")

    DB_PATH.unlink(missing_ok=True)
    con = duckdb.connect(str(DB_PATH))
    schema.build_raw(con, CSV_PATH, EVENTS_CSV_PATH, schema="raw")
    schema.build_modeled(con, CSV_PATH, EVENTS_CSV_PATH, schema="modeled")

    q = lambda sql: con.execute(sql).fetchone()[0]  # noqa: E731
    print(f"Wrote {DB_PATH}")
    print(f"  raw.users:                  {q('SELECT count(*) FROM raw.users')} rows (no docs, no views)")
    print(f"  raw.events:                 {q('SELECT count(*) FROM raw.events')} rows")
    print(f"  modeled.users:              {q('SELECT count(*) FROM modeled.users')} rows (commented catalog)")
    print(f"  modeled.events:             {q('SELECT count(*) FROM modeled.events')} rows (commented catalog)")
    print(f"  modeled.metrics_dictionary: {len(schema.METRICS_DICTIONARY)} metrics")
    print(f"  modeled.vw_active_users:    {q('SELECT count(*) FROM modeled.vw_active_users')} rows")
    print(f"  modeled.vw_qualifying_events: {q('SELECT count(*) FROM modeled.vw_qualifying_events')} rows")
    con.close()


if __name__ == "__main__":
    main()
