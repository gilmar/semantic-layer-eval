"""Steelman v2 fingerprints, computed in DuckDB SQL against the raw tables.

data/generate_data.py computes the same blocks in Python. The two are
independent implementations of each interpretation, so parity between
them (checked before any API call) is evidence that every labeled
reading is the reading its name claims.

Vector fingerprints are {group: count} dicts: plans for D1, "YYYY-MM"
months for E1.
"""

import json
from pathlib import Path

import arms

ROOT = Path(__file__).resolve().parents[1]
FINGERPRINTS_JSON = ROOT / "data" / "fingerprints.json"

PLANS = ("free", "pro", "enterprise")
PAID = "u.plan IN ('pro','enterprise')"
PRO = "u.plan = 'pro'"
STATUS_ACTIVE = "u.status = 'active'"
VALID = "NOT u.is_deleted AND NOT u.is_test_account"
QUAL = "e.event_type NOT IN ('email_opened','webhook_ping')"
JUNE = "e.occurred_at >= TIMESTAMP '2026-06-01' AND e.occurred_at < TIMESTAMP '2026-07-01'"
JUNE_BETWEEN = "e.occurred_at BETWEEN TIMESTAMP '2026-06-01' AND TIMESTAMP '2026-06-30'"
JOIN = "FROM events e JOIN users u ON u.user_id = e.user_id"
Q2_MONTHS = (("2026-04", "2026-04-01", "2026-05-01"),
             ("2026-05", "2026-05-01", "2026-06-01"),
             ("2026-06", "2026-06-01", "2026-07-01"))


def _where(*conds):
    return " WHERE " + " AND ".join(conds) if conds else ""


def _scalar(con, sql):
    return con.execute(sql).fetchone()[0]


def _distinct_users(con, *conds):
    return _scalar(con, f"SELECT count(DISTINCT e.user_id) {JOIN}{_where(*conds)}")


def _rows(con, *conds):
    return _scalar(con, f"SELECT count(*) {JOIN}{_where(*conds)}")


def b1(con, mau_fp):
    return {
        "governed_pro_mau": _distinct_users(con, VALID, QUAL, JUNE, PRO),
        "paid_plan_mau": _distinct_users(con, VALID, QUAL, JUNE, PAID),
        "pro_mau_incl_passive_events": _distinct_users(con, VALID, JUNE, PRO),
        "pro_mau_no_exclusions": _distinct_users(con, QUAL, JUNE, PRO),
        "pro_mau_requiring_status_active": _distinct_users(
            con, VALID, QUAL, JUNE, PRO, STATUS_ACTIVE),
        "pro_mau_between_boundary_bug": _distinct_users(con, VALID, QUAL, JUNE_BETWEEN, PRO),
        "pro_status_active_count": _scalar(
            con, f"SELECT count(*) FROM users u{_where(VALID, PRO, STATUS_ACTIVE)}"),
        "event_rows_pro_june": _rows(con, VALID, QUAL, JUNE, PRO),
        "confused_with_governed_mau": mau_fp["governed_mau"],
    }


def c1(con, mau_fp):
    numerators = {
        "governed_paid_mau": _distinct_users(con, VALID, QUAL, JUNE, PAID),
        "paid_mau_incl_passive": _distinct_users(con, VALID, JUNE, PAID),
        "paid_mau_no_exclusions": _distinct_users(con, QUAL, JUNE, PAID),
        "pro_mau": _distinct_users(con, VALID, QUAL, JUNE, PRO),
    }
    denominators = {
        "valid_paid_users": _scalar(con, f"SELECT count(*) FROM users u{_where(VALID, PAID)}"),
        "all_paid_rows": _scalar(con, f"SELECT count(*) FROM users u{_where(PAID)}"),
        "valid_universe": _scalar(con, f"SELECT count(*) FROM users u{_where(VALID)}"),
    }
    fp = {
        f"{nk} / {dk}": 100.0 * nv / dv
        for nk, nv in numerators.items()
        for dk, dv in denominators.items()
    }
    valid_pro = _scalar(con, f"SELECT count(*) FROM users u{_where(VALID, PRO)}")
    fp["pro_mau / valid_pro_users"] = 100.0 * numerators["pro_mau"] / valid_pro
    fp["inverted: governed_paid_mau / governed_mau"] = (
        100.0 * numerators["governed_paid_mau"] / mau_fp["governed_mau"])
    return fp


def _per_plan(con, select, *conds):
    rows = con.execute(
        f"SELECT u.plan, {select} {JOIN}{_where(*conds)} GROUP BY 1").fetchall()
    found = dict(rows)
    return {p: int(found.get(p, 0)) for p in PLANS}


def d1(con):
    distinct = "count(DISTINCT e.user_id)"
    status = dict(con.execute(
        f"SELECT u.plan, count(*) FROM users u{_where(VALID, STATUS_ACTIVE)} GROUP BY 1"
    ).fetchall())
    return {
        "governed_mau_by_plan": _per_plan(con, distinct, VALID, QUAL, JUNE),
        "mau_by_plan_incl_passive_events": _per_plan(con, distinct, VALID, JUNE),
        "mau_by_plan_no_exclusions": _per_plan(con, distinct, QUAL, JUNE),
        "mau_by_plan_between_boundary_bug": _per_plan(con, distinct, VALID, QUAL, JUNE_BETWEEN),
        "event_rows_by_plan": _per_plan(con, "count(*)", VALID, QUAL, JUNE),
        "status_active_count_by_plan": {p: int(status.get(p, 0)) for p in PLANS},
    }


def _per_month(con, select, *conds, june_between=False):
    out = {}
    for key, start, end in Q2_MONTHS:
        if june_between and key == "2026-06":
            window = JUNE_BETWEEN
        else:
            window = (f"e.occurred_at >= TIMESTAMP '{start}' "
                      f"AND e.occurred_at < TIMESTAMP '{end}'")
        out[key] = int(_scalar(con, f"SELECT {select} {JOIN}{_where(window, *conds)}"))
    return out


def e1(con):
    distinct = "count(DISTINCT e.user_id)"
    return {
        "governed_mau_by_month": _per_month(con, distinct, VALID, QUAL),
        "mau_by_month_incl_passive_events": _per_month(con, distinct, VALID),
        "mau_by_month_no_exclusions": _per_month(con, distinct, QUAL),
        "mau_by_month_between_boundary_bug": _per_month(
            con, distinct, VALID, QUAL, june_between=True),
        "event_rows_by_month": _per_month(con, "count(*)", VALID, QUAL),
        "distinct_users_whole_quarter": _distinct_users(
            con, VALID, QUAL, "e.occurred_at >= TIMESTAMP '2026-04-01'",
            "e.occurred_at < TIMESTAMP '2026-07-01'"),
    }


def _engaged(con, n, *conds):
    return _scalar(con, f"SELECT count(*) FROM (SELECT e.user_id {JOIN}{_where(*conds)} "
                        f"GROUP BY 1 HAVING count(*) >= {n})")


def f1(con, mau_fp):
    return {
        "governed_engaged_users": _engaged(con, 3, VALID, QUAL, JUNE),
        "engaged_strictly_more_than_3": _engaged(con, 4, VALID, QUAL, JUNE),
        "engaged_incl_passive_events": _engaged(con, 3, VALID, JUNE),
        "engaged_no_exclusions": _engaged(con, 3, QUAL, JUNE),
        "engaged_all_time": _engaged(con, 3, VALID, QUAL),
        "engaged_between_boundary_bug": _engaged(con, 3, VALID, QUAL, JUNE_BETWEEN),
        "event_rows_qualifying_june_valid": _rows(con, VALID, QUAL, JUNE),
        "confused_with_governed_mau": mau_fp["governed_mau"],
    }


def compute_all() -> dict:
    """Every fingerprint block v2 scores against, keyed like fingerprints.json."""
    count_fp = arms.compute_count_fingerprints()
    mau_fp = arms.compute_mau_fingerprints(count_fp)
    wau_fp = arms.compute_wau_fingerprints(count_fp, mau_fp)
    con = arms._connect_raw()
    try:
        return {
            "active_count": count_fp,
            "mau_june_2026": mau_fp,
            "wau_week_of_2026_06_22": wau_fp,
            "b1_pro_mau_june_2026": b1(con, mau_fp),
            "c1_paying_active_share_june_2026": c1(con, mau_fp),
            "d1_mau_by_plan_june_2026": d1(con),
            "e1_mau_by_month_q2_2026": e1(con),
            "f1_engaged_users_june_2026": f1(con, mau_fp),
        }
    finally:
        con.close()


def parity_errors(computed: dict) -> list:
    """Differences between the harness (SQL) and the generator (Python).
    Keys only the harness has (login-recency readings computed live
    against CURRENT_DATE) are allowed."""
    stored = json.loads(FINGERPRINTS_JSON.read_text())
    errors = []
    for block, expected in stored.items():
        got = computed.get(block)
        if got is None:
            errors.append(f"{block}: missing from harness")
            continue
        for key, value in expected.items():
            if key not in got:
                errors.append(f"{block}.{key}: missing from harness")
            elif isinstance(value, float):
                if abs(got[key] - value) > 1e-9:
                    errors.append(f"{block}.{key}: harness {got[key]} != generator {value}")
            elif got[key] != value:
                errors.append(f"{block}.{key}: harness {got[key]} != generator {value}")
    return errors
