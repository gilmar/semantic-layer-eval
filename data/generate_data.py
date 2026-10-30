"""Generate data/users.csv and data/events.csv with deliberate metric ambiguity.

The datasets are designed so that every plausible interpretation of the
eval questions yields a DIFFERENT number. The answer a model gives
therefore fingerprints the interpretation it chose.

users.csv traps (for "how many active users" and "what share is active"):
  1. status has four values (active, inactive, pending, churned)
  2. is_deleted marks soft-deleted rows that must be excluded
  3. is_test_account marks QA accounts that must be excluded
  4. plan (free / pro / enterprise) invites a "paying = active" reading
  5. last_login_at invites a "logged in recently = active" reading

events.csv traps (for "monthly active users in June 2026"):
  6. joining users to events multiplies rows: COUNT(*) vs COUNT(DISTINCT)
  7. passive (email_opened) and machine (webhook_ping) events must be
     excluded from qualifying activity
  8. test accounts are noisy bots and deleted users have history, so
     population exclusions visibly change the number
  9. occurred_at is a TIMESTAMP: BETWEEN '2026-06-01' AND '2026-06-30'
     silently drops most of June 30 (the classic boundary bug)
 10. MAU is behavioral; the status column is administrative. A few valid
     churned/inactive users have qualifying June events, so conflating
     the two definitions produces a different number

events.csv traps added by the second time grain ("weekly active users
in the week of June 22, 2026"). Every MAU trap above carries over, and
the week adds its own:
 11. week start: the governed week is the ISO week, Monday through
     Sunday (June 22-28). A Sunday-start week (June 21-27) is a
     different population
 12. BETWEEN '2026-06-22' AND '2026-06-28' drops most of Sunday June 28,
     and the Sunday-start variant has its own boundary bug
 13. week(occurred_at) = 26 without a year filter pulls in week 26 of
     2023, 2024, and 2025
 14. WAU is easily answered with the June MAU or the active-user count

Official (governed) definitions:
  active user: status = 'active' AND is_deleted = false AND is_test_account = false
  MAU (month M): DISTINCT valid users (not deleted, not test) with at
    least one qualifying event (not email_opened, not webhook_ping)
    with occurred_at in calendar month M
  WAU (week W): the same, with occurred_at in ISO week W (Monday 00:00
    up to the following Monday 00:00)

Deterministic: single seed, users are generated first so extending the
random stream with events leaves the random user attributes unchanged.
Crafted separators (event groups A-E, plan swap P) are deterministic
rules applied afterwards. Stdlib only.
"""

import csv
import json
import random
from datetime import date, datetime, timedelta
from pathlib import Path

SEED = 42
N_USERS = 300
REFERENCE_DATE = date(2026, 7, 1)

STATUSES = ["active", "inactive", "pending", "churned"]
STATUS_WEIGHTS = [0.55, 0.20, 0.10, 0.15]
PLANS = ["free", "pro", "enterprise"]
PLAN_WEIGHTS = [0.50, 0.35, 0.15]
P_DELETED = 0.08
P_TEST = 0.05

FIRST_NAMES = [
    "Ana", "Bruno", "Carla", "Diego", "Elisa", "Felipe", "Gabriela", "Hugo",
    "Isabela", "Joao", "Karen", "Lucas", "Mariana", "Nicolas", "Olivia",
    "Pedro", "Renata", "Sofia", "Thiago", "Vitor",
]
LAST_NAMES = [
    "Almeida", "Barbosa", "Costa", "Dias", "Ferreira", "Gomes", "Lima",
    "Martins", "Nunes", "Oliveira", "Pereira", "Ribeiro", "Santos", "Silva",
    "Souza", "Teixeira", "Vieira", "Rocha", "Cardoso", "Moraes",
]

QUALIFYING_EVENT_TYPES = ["login", "card_created", "card_moved", "report_viewed", "api_call"]
NON_QUALIFYING_EVENT_TYPES = ["email_opened", "webhook_ping"]

JUNE_START = datetime(2026, 6, 1)
JULY_START = datetime(2026, 7, 1)
JUNE_30_MIDNIGHT = datetime(2026, 6, 30)  # what BETWEEN casts '2026-06-30' to

# The WAU question names the week of June 22, 2026 (a Monday, ISO week 26).
WEEK_START = datetime(2026, 6, 22)
WEEK_END = datetime(2026, 6, 29)  # exclusive
WEEK_SUNDAY_MIDNIGHT = datetime(2026, 6, 28)  # what BETWEEN casts '2026-06-28' to
SUNDAY_WEEK_START = datetime(2026, 6, 21)
SUNDAY_WEEK_END = datetime(2026, 6, 28)  # exclusive
SUNDAY_WEEK_SATURDAY_MIDNIGHT = datetime(2026, 6, 27)
ISO_WEEK = 26


# ------------------------------------------------------------------ users

def generate_rows():
    random.seed(SEED)
    rows = []
    for i in range(1, N_USERS + 1):
        first = random.choice(FIRST_NAMES)
        last = random.choice(LAST_NAMES)
        status = random.choices(STATUSES, STATUS_WEIGHTS)[0]
        is_test = random.random() < P_TEST
        is_deleted = random.random() < P_DELETED
        plan = random.choices(PLANS, PLAN_WEIGHTS)[0]
        domain = "qa.example.com" if is_test else "acme-corp.com"
        email = f"{first.lower()}.{last.lower()}{i}@{domain}"

        created_at = REFERENCE_DATE - timedelta(days=random.randint(30, 1200))

        if status == "pending":
            last_login_at = None  # signed up, never logged in
        elif status == "active":
            last_login_at = REFERENCE_DATE - timedelta(days=random.randint(0, 120))
        else:
            last_login_at = REFERENCE_DATE - timedelta(days=random.randint(60, 700))

        if last_login_at is not None and last_login_at < created_at:
            last_login_at = created_at

        rows.append({
            "user_id": i,
            "email": email,
            "full_name": f"{first} {last}",
            "status": status,
            "is_deleted": is_deleted,
            "is_test_account": is_test,
            "plan": plan,
            "created_at": created_at.isoformat(),
            "last_login_at": last_login_at.isoformat() if last_login_at else "",
        })
    return rows


# ----------------------------------------------------------------- events

def _random_event_datetime(start: date, end: date) -> datetime:
    span = max((end - start).days, 1)
    d = start + timedelta(days=random.randint(0, span))
    return datetime(d.year, d.month, d.day,
                    random.randint(0, 23), random.randint(0, 59),
                    random.randint(0, 59))


def _random_event_type() -> str:
    if random.random() < 0.25:
        return random.choice(NON_QUALIFYING_EVENT_TYPES)
    return random.choice(QUALIFYING_EVENT_TYPES)


def generate_events(users):
    """Random events per user, then deterministic crafted separators.

    Crafted users guarantee the MAU and WAU fingerprints stay distinct:
      A (2 users)  their ONLY qualifying June event is on June 30 in the
                   afternoon, so the BETWEEN boundary bug loses them
      B (3 users)  valid but churned/inactive, with a qualifying api_call
                   in the ISO week of June 22, so behavioral MAU and
                   WAU != status-based counts. The third lands on Sunday
                   June 28 in the afternoon, so the Monday-start BETWEEN
                   bug and the Sunday-start week both lose that user
      C (2 users)  valid, no June events at all, given one passive
                   email_opened in June, so including passive events
                   changes the number
      D (2 users)  valid and status active, all their June events
                   removed (on vacation), so behavioral MAU and the
                   status-based active count cannot coincide
      E (1 user)   steelman v2, engaged users: valid, exactly three
                   qualifying June events, the latest moved to June 30
                   in the afternoon, so the BETWEEN bug drops the user
                   below the threshold. Earlier June events remain, so
                   every MAU, WAU, and per-month count is unchanged
    """
    events = []

    def add(user_id, event_type, occurred: datetime):
        events.append({
            "event_id": len(events) + 1,
            "user_id": user_id,
            "event_type": event_type,
            "occurred_at": occurred.strftime("%Y-%m-%d %H:%M:%S"),
        })

    for u in users:
        created = date.fromisoformat(u["created_at"])
        status = u["status"]
        if u["is_test_account"]:
            n, window_end = random.randint(80, 140), REFERENCE_DATE  # noisy QA bots
        elif status == "pending":
            n, window_end = 0, REFERENCE_DATE
        elif status == "active":
            n, window_end = random.randint(15, 50), REFERENCE_DATE
        else:  # inactive, churned: history ends around their last login
            ll = u["last_login_at"]
            n = random.randint(3, 20)
            window_end = date.fromisoformat(ll) if ll else created

        span = max((window_end - created).days, 1)
        for _ in range(n):
            if status == "active" and not u["is_test_account"] and random.random() < 0.6:
                # recency skew for active users
                d = window_end - timedelta(days=random.randint(0, min(45, span)))
                occurred = datetime(d.year, d.month, d.day,
                                    random.randint(0, 23), random.randint(0, 59),
                                    random.randint(0, 59))
            else:
                occurred = _random_event_datetime(created, window_end)
            add(u["user_id"], _random_event_type(), occurred)

    # ---- deterministic crafted separators (no randomness below) ----
    def june_events(uid, qualifying_only):
        for e in events:
            if e["user_id"] != uid:
                continue
            occurred = datetime.strptime(e["occurred_at"], "%Y-%m-%d %H:%M:%S")
            if not (JUNE_START <= occurred < JULY_START):
                continue
            if qualifying_only and e["event_type"] not in QUALIFYING_EVENT_TYPES:
                continue
            return True
        return False

    valid = [u for u in users if not u["is_deleted"] and not u["is_test_account"]]

    b_users = sorted(
        (u for u in valid
         if u["status"] in ("inactive", "churned")
         and not june_events(u["user_id"], qualifying_only=True)),
        key=lambda u: u["user_id"],
    )[:3]
    b_times = [datetime(2026, 6, 23, 11, 0, 0), datetime(2026, 6, 24, 11, 0, 0),
               datetime(2026, 6, 28, 15, 30, 0)]
    for u, occurred in zip(b_users, b_times):
        add(u["user_id"], "api_call", occurred)

    taken = {u["user_id"] for u in b_users}
    a_users = sorted(
        (u for u in valid
         if u["user_id"] not in taken
         and u["status"] != "pending"
         and not june_events(u["user_id"], qualifying_only=True)),
        key=lambda u: u["user_id"],
    )[:2]
    for i, u in enumerate(a_users):
        add(u["user_id"], "card_created", datetime(2026, 6, 30, 15 + i, 30, 0))

    taken |= {u["user_id"] for u in a_users}
    c_users = sorted(
        (u for u in valid
         if u["user_id"] not in taken
         and not june_events(u["user_id"], qualifying_only=False)),
        key=lambda u: u["user_id"],
    )[:2]
    for i, u in enumerate(c_users):
        add(u["user_id"], "email_opened", datetime(2026, 6, 15 + i, 9, 0, 0))

    taken |= {u["user_id"] for u in c_users}
    d_users = sorted(
        (u for u in valid
         if u["user_id"] not in taken
         and u["status"] == "active"
         and june_events(u["user_id"], qualifying_only=True)),
        key=lambda u: -u["user_id"],
    )[:2]
    d_ids = {u["user_id"] for u in d_users}

    def in_june_str(e):
        occurred = datetime.strptime(e["occurred_at"], "%Y-%m-%d %H:%M:%S")
        return JUNE_START <= occurred < JULY_START

    events[:] = [e for e in events
                 if not (e["user_id"] in d_ids and in_june_str(e))]

    taken |= d_ids
    week_windows_start = datetime(2026, 6, 21)  # earliest WAU window (Sunday start)
    for u in sorted(valid, key=lambda u: u["user_id"]):
        if u["user_id"] in taken:
            continue
        qualifying = [e for e in events
                      if e["user_id"] == u["user_id"]
                      and e["event_type"] in QUALIFYING_EVENT_TYPES
                      and in_june_str(e)]
        if len(qualifying) != ENGAGED_MIN_EVENTS:
            continue
        if any(e["occurred_at"] >= week_windows_start.strftime("%Y-%m-%d")
               for e in qualifying):
            continue
        max(qualifying, key=lambda e: e["occurred_at"])["occurred_at"] = (
            "2026-06-30 16:45:00")
        break

    for i, e in enumerate(events, start=1):
        e["event_id"] = i

    return events


def apply_plan_separators(users, events):
    """Crafted plan swap P (steelman v2, pro-plan MAU).

    One valid inactive/churned free user with a qualifying June event
    (before June 30) moves to pro; one valid inactive/churned pro user
    with no June events moves to free. Without it the pro-plan BETWEEN
    bug and the pro status-active count coincide. Both users are
    non-active, so the v1 counts that read plan (any paying plan, paying
    and active) are unchanged.
    """
    june_any, june_qualifying = set(), set()
    for e in events:
        ts = datetime.strptime(e["occurred_at"], "%Y-%m-%d %H:%M:%S")
        if JUNE_START <= ts < JULY_START:
            june_any.add(e["user_id"])
            if e["event_type"] in QUALIFYING_EVENT_TYPES and ts < JUNE_30_MIDNIGHT:
                june_qualifying.add(e["user_id"])

    def lapsed(u):
        return (not u["is_deleted"] and not u["is_test_account"]
                and u["status"] in ("inactive", "churned"))

    to_pro = min((u for u in users if lapsed(u) and u["plan"] == "free"
                  and u["user_id"] in june_qualifying),
                 key=lambda u: u["user_id"])
    to_free = min((u for u in users if lapsed(u) and u["plan"] == "pro"
                   and u["user_id"] not in june_any),
                  key=lambda u: u["user_id"])
    to_pro["plan"], to_free["plan"] = "pro", "free"


# ------------------------------------------------------------ fingerprints

def compute_count_fingerprints(rows):
    """Count under every plausible interpretation of 'active users'."""
    def cnt(pred):
        return sum(1 for r in rows if pred(r))

    governed = cnt(lambda r: r["status"] == "active"
                   and not r["is_deleted"] and not r["is_test_account"])

    fp = {
        "total_rows": len(rows),
        "status_active_only": cnt(lambda r: r["status"] == "active"),
        "status_active_excl_deleted": cnt(
            lambda r: r["status"] == "active" and not r["is_deleted"]),
        "status_active_excl_test": cnt(
            lambda r: r["status"] == "active" and not r["is_test_account"]),
        "governed_active_users": governed,
        "any_status_excl_deleted": cnt(lambda r: not r["is_deleted"]),
        "valid_universe": cnt(
            lambda r: not r["is_deleted"] and not r["is_test_account"]),
        "active_or_pending_excl_deleted_test": cnt(
            lambda r: r["status"] in ("active", "pending")
            and not r["is_deleted"] and not r["is_test_account"]),
        "not_churned_not_inactive": cnt(
            lambda r: r["status"] in ("active", "pending")),
        "paying_plan_any_status": cnt(lambda r: r["plan"] != "free"),
        "paying_and_status_active": cnt(
            lambda r: r["plan"] != "free" and r["status"] == "active"),
    }
    _assert_distinct(fp, "count")
    return fp


class _EventIndex:
    """Parsed events plus the population predicates every activity
    fingerprint is built from."""

    def __init__(self, users, events):
        self.by_id = {u["user_id"]: u for u in users}
        self.qual = set(QUALIFYING_EVENT_TYPES)
        self.parsed = [
            (e["user_id"], e["event_type"],
             datetime.strptime(e["occurred_at"], "%Y-%m-%d %H:%M:%S"))
            for e in events
        ]

    def valid(self, uid):
        u = self.by_id[uid]
        return not u["is_deleted"] and not u["is_test_account"]

    def users_where(self, pred):
        return len({uid for uid, et, ts in self.parsed if pred(uid, et, ts)})

    def rows_where(self, pred):
        return sum(1 for uid, et, ts in self.parsed if pred(uid, et, ts))


def compute_mau_fingerprints(users, events, count_fp):
    """June 2026 MAU under every plausible interpretation."""
    ix = _EventIndex(users, events)
    by_id, qual, valid = ix.by_id, ix.qual, ix.valid
    users_where, rows_where = ix.users_where, ix.rows_where

    in_june = lambda ts: JUNE_START <= ts < JULY_START  # noqa: E731

    fp = {
        "governed_mau": users_where(
            lambda uid, et, ts: in_june(ts) and et in qual and valid(uid)),
        "mau_incl_passive_events": users_where(
            lambda uid, et, ts: in_june(ts) and valid(uid)),
        "mau_incl_test_and_deleted": users_where(
            lambda uid, et, ts: in_june(ts) and et in qual),
        "mau_all_users_all_events": users_where(
            lambda uid, et, ts: in_june(ts)),
        "mau_between_boundary_bug": users_where(
            lambda uid, et, ts: JUNE_START <= ts <= JUNE_30_MIDNIGHT
            and et in qual and valid(uid)),
        "mau_requiring_status_active": users_where(
            lambda uid, et, ts: in_june(ts) and et in qual and valid(uid)
            and by_id[uid]["status"] == "active"),
        "event_rows_qualifying_june_valid": rows_where(
            lambda uid, et, ts: in_june(ts) and et in qual and valid(uid)),
        "event_rows_all_june": rows_where(lambda uid, et, ts: in_june(ts)),
        "confused_with_status_active_count": count_fp["status_active_only"],
        "confused_with_governed_active_users": count_fp["governed_active_users"],
    }
    _assert_distinct(fp, "MAU")
    return fp


def compute_wau_fingerprints(users, events, count_fp, mau_fp):
    """WAU for the week of June 22, 2026 under every plausible
    interpretation. Each MAU trap reappears at the new grain, and the
    week adds its own: week start, a Sunday boundary, and week numbers
    that repeat every year."""
    ix = _EventIndex(users, events)
    by_id, qual, valid = ix.by_id, ix.qual, ix.valid
    users_where, rows_where = ix.users_where, ix.rows_where

    in_week = lambda ts: WEEK_START <= ts < WEEK_END  # noqa: E731

    fp = {
        "governed_wau": users_where(
            lambda uid, et, ts: in_week(ts) and et in qual and valid(uid)),
        "wau_incl_passive_events": users_where(
            lambda uid, et, ts: in_week(ts) and valid(uid)),
        "wau_incl_test_and_deleted": users_where(
            lambda uid, et, ts: in_week(ts) and et in qual),
        "wau_all_users_all_events": users_where(
            lambda uid, et, ts: in_week(ts)),
        "wau_between_boundary_bug": users_where(
            lambda uid, et, ts: WEEK_START <= ts <= WEEK_SUNDAY_MIDNIGHT
            and et in qual and valid(uid)),
        "wau_sunday_start_week": users_where(
            lambda uid, et, ts: SUNDAY_WEEK_START <= ts < SUNDAY_WEEK_END
            and et in qual and valid(uid)),
        "wau_sunday_start_between_bug": users_where(
            lambda uid, et, ts: SUNDAY_WEEK_START <= ts <= SUNDAY_WEEK_SATURDAY_MIDNIGHT
            and et in qual and valid(uid)),
        "wau_week_number_any_year": users_where(
            lambda uid, et, ts: ts.isocalendar()[1] == ISO_WEEK
            and et in qual and valid(uid)),
        "wau_requiring_status_active": users_where(
            lambda uid, et, ts: in_week(ts) and et in qual and valid(uid)
            and by_id[uid]["status"] == "active"),
        "wau_status_active_incl_passive": users_where(
            lambda uid, et, ts: in_week(ts) and valid(uid)
            and by_id[uid]["status"] == "active"),
        "event_rows_qualifying_week_valid": rows_where(
            lambda uid, et, ts: in_week(ts) and et in qual and valid(uid)),
        "event_rows_all_week": rows_where(lambda uid, et, ts: in_week(ts)),
        "confused_with_governed_mau": mau_fp["governed_mau"],
        "confused_with_status_active_count": count_fp["status_active_only"],
        "confused_with_governed_active_users": count_fp["governed_active_users"],
    }
    _assert_distinct(fp, "WAU")
    return fp


# ------------------------------------------------------ v2 fingerprints
#
# Steelman v2 (docs/steelman-v2-spec.md) asks compositions of the same
# governed primitives. Answers can be counts, percentages, or vectors
# keyed by plan or month; vector fingerprints must differ as whole
# vectors, not just in their sums.

PAID_PLANS = ("pro", "enterprise")
Q2_MONTHS = (
    ("2026-04", datetime(2026, 4, 1), datetime(2026, 5, 1)),
    ("2026-05", datetime(2026, 5, 1), JUNE_START),
    ("2026-06", JUNE_START, JULY_START),
)
ENGAGED_MIN_EVENTS = 3
SHARE_MIN_GAP_PP = 0.06
V2_GROUND_TRUTH = {
    "b1_pro_mau_june_2026": "governed_pro_mau",
    "c1_paying_active_share_june_2026": "governed_paid_mau / valid_paid_users",
    "d1_mau_by_plan_june_2026": "governed_mau_by_plan",
    "e1_mau_by_month_q2_2026": "governed_mau_by_month",
    "f1_engaged_users_june_2026": "governed_engaged_users",
}


def _valid_user_count(users, pred=lambda u: True):
    return sum(1 for u in users
               if not u["is_deleted"] and not u["is_test_account"] and pred(u))


def compute_b1_fingerprints(users, events, mau_fp):
    """B1: June 2026 MAU on the pro plan. Plan lives on users, activity
    on events, so every MAU trap reappears behind a join."""
    ix = _EventIndex(users, events)
    by_id, qual, valid = ix.by_id, ix.qual, ix.valid
    users_where, rows_where = ix.users_where, ix.rows_where
    in_june = lambda ts: JUNE_START <= ts < JULY_START  # noqa: E731
    pro = lambda uid: by_id[uid]["plan"] == "pro"  # noqa: E731

    fp = {
        "governed_pro_mau": users_where(
            lambda uid, et, ts: in_june(ts) and et in qual and valid(uid) and pro(uid)),
        "paid_plan_mau": users_where(
            lambda uid, et, ts: in_june(ts) and et in qual and valid(uid)
            and by_id[uid]["plan"] in PAID_PLANS),
        "pro_mau_incl_passive_events": users_where(
            lambda uid, et, ts: in_june(ts) and valid(uid) and pro(uid)),
        "pro_mau_no_exclusions": users_where(
            lambda uid, et, ts: in_june(ts) and et in qual and pro(uid)),
        "pro_mau_requiring_status_active": users_where(
            lambda uid, et, ts: in_june(ts) and et in qual and valid(uid) and pro(uid)
            and by_id[uid]["status"] == "active"),
        "pro_mau_between_boundary_bug": users_where(
            lambda uid, et, ts: JUNE_START <= ts <= JUNE_30_MIDNIGHT
            and et in qual and valid(uid) and pro(uid)),
        "pro_status_active_count": _valid_user_count(
            users, lambda u: u["plan"] == "pro" and u["status"] == "active"),
        "event_rows_pro_june": rows_where(
            lambda uid, et, ts: in_june(ts) and et in qual and valid(uid) and pro(uid)),
        "confused_with_governed_mau": mau_fp["governed_mau"],
    }
    _assert_distinct(fp, "B1")
    return fp


def compute_c1_fingerprints(users, events, mau_fp):
    """C1: share of paying users who were monthly active in June 2026,
    as percentages. No measure or view defines this ratio; both parts
    are governed primitives."""
    ix = _EventIndex(users, events)
    by_id, qual, valid = ix.by_id, ix.qual, ix.valid
    users_where = ix.users_where
    in_june = lambda ts: JUNE_START <= ts < JULY_START  # noqa: E731
    paid = lambda uid: by_id[uid]["plan"] in PAID_PLANS  # noqa: E731

    numerators = {
        "governed_paid_mau": users_where(
            lambda uid, et, ts: in_june(ts) and et in qual and valid(uid) and paid(uid)),
        "paid_mau_incl_passive": users_where(
            lambda uid, et, ts: in_june(ts) and valid(uid) and paid(uid)),
        "paid_mau_no_exclusions": users_where(
            lambda uid, et, ts: in_june(ts) and et in qual and paid(uid)),
        "pro_mau": users_where(
            lambda uid, et, ts: in_june(ts) and et in qual and valid(uid)
            and by_id[uid]["plan"] == "pro"),
    }
    denominators = {
        "valid_paid_users": _valid_user_count(users, lambda u: u["plan"] in PAID_PLANS),
        "all_paid_rows": sum(1 for u in users if u["plan"] in PAID_PLANS),
        "valid_universe": _valid_user_count(users),
    }
    fp = {
        f"{nk} / {dk}": 100.0 * nv / dv
        for nk, nv in numerators.items()
        for dk, dv in denominators.items()
    }
    fp["pro_mau / valid_pro_users"] = 100.0 * numerators["pro_mau"] / _valid_user_count(
        users, lambda u: u["plan"] == "pro")
    fp["inverted: governed_paid_mau / governed_mau"] = (
        100.0 * numerators["governed_paid_mau"] / mau_fp["governed_mau"])
    _assert_separated(fp, "C1", SHARE_MIN_GAP_PP)
    return fp


def compute_d1_fingerprints(users, events):
    """D1: June 2026 MAU broken down by plan, as {plan: count}."""
    ix = _EventIndex(users, events)
    by_id, qual, valid = ix.by_id, ix.qual, ix.valid
    users_where, rows_where = ix.users_where, ix.rows_where
    in_june = lambda ts: JUNE_START <= ts < JULY_START  # noqa: E731

    def per_plan(count, pred):
        return {p: count(lambda uid, et, ts, p=p: by_id[uid]["plan"] == p
                         and pred(uid, et, ts))
                for p in PLANS}

    fp = {
        "governed_mau_by_plan": per_plan(users_where, lambda uid, et, ts:
                                         in_june(ts) and et in qual and valid(uid)),
        "mau_by_plan_incl_passive_events": per_plan(users_where, lambda uid, et, ts:
                                                    in_june(ts) and valid(uid)),
        "mau_by_plan_no_exclusions": per_plan(users_where, lambda uid, et, ts:
                                              in_june(ts) and et in qual),
        "mau_by_plan_between_boundary_bug": per_plan(users_where, lambda uid, et, ts:
                                                     JUNE_START <= ts <= JUNE_30_MIDNIGHT
                                                     and et in qual and valid(uid)),
        "event_rows_by_plan": per_plan(rows_where, lambda uid, et, ts:
                                       in_june(ts) and et in qual and valid(uid)),
        "status_active_count_by_plan": {
            p: _valid_user_count(users, lambda u, p=p: u["plan"] == p
                                 and u["status"] == "active")
            for p in PLANS},
    }
    _assert_distinct(fp, "D1")
    return fp


def compute_e1_fingerprints(users, events):
    """E1: MAU for each month of Q2 2026, as {YYYY-MM: count}."""
    ix = _EventIndex(users, events)
    qual, valid = ix.qual, ix.valid
    users_where, rows_where = ix.users_where, ix.rows_where

    def per_month(count, pred, june_end_inclusive=False):
        out = {}
        for key, start, end in Q2_MONTHS:
            if june_end_inclusive and key == "2026-06":
                in_month = lambda ts, s=start: s <= ts <= JUNE_30_MIDNIGHT  # noqa: E731
            else:
                in_month = lambda ts, s=start, e=end: s <= ts < e  # noqa: E731
            out[key] = count(lambda uid, et, ts, m=in_month: m(ts) and pred(uid, et, ts))
        return out

    governed = lambda uid, et, ts: et in qual and valid(uid)  # noqa: E731
    fp = {
        "governed_mau_by_month": per_month(users_where, governed),
        "mau_by_month_incl_passive_events": per_month(
            users_where, lambda uid, et, ts: valid(uid)),
        "mau_by_month_no_exclusions": per_month(
            users_where, lambda uid, et, ts: et in qual),
        "mau_by_month_between_boundary_bug": per_month(
            users_where, governed, june_end_inclusive=True),
        "event_rows_by_month": per_month(rows_where, governed),
        "distinct_users_whole_quarter": users_where(
            lambda uid, et, ts: Q2_MONTHS[0][1] <= ts < JULY_START and governed(uid, et, ts)),
    }
    _assert_distinct(fp, "E1")
    return fp


def compute_f1_fingerprints(users, events, mau_fp):
    """F1: engaged users in June 2026 (valid users with at least three
    qualifying events in the calendar month). Defined in prose and as a
    measure; no view encodes it."""
    ix = _EventIndex(users, events)
    qual, valid = ix.qual, ix.valid
    in_june = lambda ts: JUNE_START <= ts < JULY_START  # noqa: E731

    def users_with_at_least(n, pred):
        per_user = {}
        for uid, et, ts in ix.parsed:
            if pred(uid, et, ts):
                per_user[uid] = per_user.get(uid, 0) + 1
        return sum(1 for c in per_user.values() if c >= n)

    governed = lambda uid, et, ts: in_june(ts) and et in qual and valid(uid)  # noqa: E731
    n = ENGAGED_MIN_EVENTS
    fp = {
        "governed_engaged_users": users_with_at_least(n, governed),
        "engaged_strictly_more_than_3": users_with_at_least(n + 1, governed),
        "engaged_incl_passive_events": users_with_at_least(
            n, lambda uid, et, ts: in_june(ts) and valid(uid)),
        "engaged_no_exclusions": users_with_at_least(
            n, lambda uid, et, ts: in_june(ts) and et in qual),
        "engaged_all_time": users_with_at_least(
            n, lambda uid, et, ts: et in qual and valid(uid)),
        "engaged_between_boundary_bug": users_with_at_least(
            n, lambda uid, et, ts: JUNE_START <= ts <= JUNE_30_MIDNIGHT
            and et in qual and valid(uid)),
        "event_rows_qualifying_june_valid": ix.rows_where(governed),
        "confused_with_governed_mau": mau_fp["governed_mau"],
    }
    _assert_distinct(fp, "F1")
    return fp


def _fingerprint_key(value):
    return tuple(sorted(value.items())) if isinstance(value, dict) else value


def _assert_distinct(fp, name):
    values = [_fingerprint_key(v) for v in fp.values()]
    if len(set(values)) != len(values):
        dupes = {v for v in values if values.count(v) > 1}
        raise SystemExit(
            f"{name} fingerprint collision on {dupes}. "
            f"Adjust SEED, probabilities, or crafted separators."
        )


def _assert_separated(fp, name, min_gap):
    ordered = sorted(fp.items(), key=lambda kv: kv[1])
    for (ka, va), (kb, vb) in zip(ordered, ordered[1:]):
        if vb - va < min_gap:
            raise SystemExit(
                f"{name} fingerprints {ka!r} ({va:.4f}) and {kb!r} ({vb:.4f}) "
                f"are closer than {min_gap}pp. Adjust crafted separators."
            )


# ------------------------------------------------------------------- main

def main():
    out_dir = Path(__file__).resolve().parent
    rows = generate_rows()
    events = generate_events(rows)
    apply_plan_separators(rows, events)

    csv_path = out_dir / "users.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        for r in rows:
            r = dict(r)
            r["is_deleted"] = "true" if r["is_deleted"] else "false"
            r["is_test_account"] = "true" if r["is_test_account"] else "false"
            writer.writerow(r)

    events_path = out_dir / "events.csv"
    with open(events_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(events[0].keys()))
        writer.writeheader()
        writer.writerows(events)

    count_fp = compute_count_fingerprints(rows)
    mau_fp = compute_mau_fingerprints(rows, events, count_fp)
    wau_fp = compute_wau_fingerprints(rows, events, count_fp, mau_fp)
    v2_fp = {
        "b1_pro_mau_june_2026": compute_b1_fingerprints(rows, events, mau_fp),
        "c1_paying_active_share_june_2026": compute_c1_fingerprints(rows, events, mau_fp),
        "d1_mau_by_plan_june_2026": compute_d1_fingerprints(rows, events),
        "e1_mau_by_month_q2_2026": compute_e1_fingerprints(rows, events),
        "f1_engaged_users_june_2026": compute_f1_fingerprints(rows, events, mau_fp),
    }

    fp_path = out_dir / "fingerprints.json"
    with open(fp_path, "w") as f:
        json.dump({"active_count": count_fp, "mau_june_2026": mau_fp,
                   "wau_week_of_2026_06_22": wau_fp, **v2_fp}, f, indent=2)

    print(f"Wrote {csv_path} ({len(rows)} rows)")
    print(f"Wrote {events_path} ({len(events)} rows)")
    print(f"Wrote {fp_path}")
    print("\nActive users: interpretation -> count (all distinct):")
    for k, v in sorted(count_fp.items(), key=lambda kv: -kv[1]):
        marker = "  <-- GROUND TRUTH" if k == "governed_active_users" else ""
        print(f"  {v:5d}  {k}{marker}")
    print("\nMAU June 2026: interpretation -> count (all distinct):")
    for k, v in sorted(mau_fp.items(), key=lambda kv: -kv[1]):
        marker = "  <-- GROUND TRUTH" if k == "governed_mau" else ""
        print(f"  {v:5d}  {k}{marker}")
    print("\nWAU week of June 22, 2026: interpretation -> count (all distinct):")
    for k, v in sorted(wau_fp.items(), key=lambda kv: -kv[1]):
        marker = "  <-- GROUND TRUTH" if k == "governed_wau" else ""
        print(f"  {v:5d}  {k}{marker}")
    for name, fp in v2_fp.items():
        print(f"\nSteelman v2 {name}: interpretation -> value (all distinct):")
        for k, v in fp.items():
            marker = "  <-- GROUND TRUTH" if k == V2_GROUND_TRUTH[name] else ""
            shown = f"{v:9.4f}" if isinstance(v, float) else v
            print(f"  {shown}  {k}{marker}")


if __name__ == "__main__":
    main()
