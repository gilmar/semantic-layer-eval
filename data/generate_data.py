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

Official (governed) definitions:
  active user: status = 'active' AND is_deleted = false AND is_test_account = false
  MAU (month M): DISTINCT valid users (not deleted, not test) with at
    least one qualifying event (not email_opened, not webhook_ping)
    with occurred_at in calendar month M

Deterministic: single seed, users are generated first so extending the
random stream with events leaves users.csv byte-identical to earlier
versions. Stdlib only.
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

    Crafted users guarantee the MAU fingerprints stay distinct:
      A (2 users)  their ONLY qualifying June event is on June 30 in the
                   afternoon, so the BETWEEN boundary bug loses them
      B (3 users)  valid but churned/inactive, with a qualifying June
                   api_call, so behavioral MAU != status-based counts
      C (2 users)  valid, no June events at all, given one passive
                   email_opened in June, so including passive events
                   changes the number
      D (2 users)  valid and status active, all their June events
                   removed (on vacation), so behavioral MAU and the
                   status-based active count cannot coincide
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
    for i, u in enumerate(b_users):
        add(u["user_id"], "api_call", datetime(2026, 6, 10 + i, 11, 0, 0))

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

    for i, e in enumerate(events, start=1):
        e["event_id"] = i

    return events


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


def compute_mau_fingerprints(users, events, count_fp):
    """June 2026 MAU under every plausible interpretation."""
    by_id = {u["user_id"]: u for u in users}
    qual = set(QUALIFYING_EVENT_TYPES)

    parsed = [
        (e["user_id"], e["event_type"],
         datetime.strptime(e["occurred_at"], "%Y-%m-%d %H:%M:%S"))
        for e in events
    ]

    def valid(uid):
        u = by_id[uid]
        return not u["is_deleted"] and not u["is_test_account"]

    def users_where(pred):
        return len({uid for uid, et, ts in parsed if pred(uid, et, ts)})

    def rows_where(pred):
        return sum(1 for uid, et, ts in parsed if pred(uid, et, ts))

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


def _assert_distinct(fp, name):
    values = list(fp.values())
    if len(set(values)) != len(values):
        dupes = {v for v in values if values.count(v) > 1}
        raise SystemExit(
            f"{name} fingerprint collision on {dupes}. "
            f"Adjust SEED, probabilities, or crafted separators."
        )


# ------------------------------------------------------------------- main

def main():
    out_dir = Path(__file__).resolve().parent
    rows = generate_rows()
    events = generate_events(rows)

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

    fp_path = out_dir / "fingerprints.json"
    with open(fp_path, "w") as f:
        json.dump({"active_count": count_fp, "mau_june_2026": mau_fp}, f, indent=2)

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


if __name__ == "__main__":
    main()
