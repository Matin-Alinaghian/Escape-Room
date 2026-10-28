"""Regression tests for the admin waitlist (لیست انتظار) and Jalali dates.

Run:  PYTHONIOENCODING=utf-8 python tests_waitlist.py

Covers:
  * a public booking request (status ``pending``) shows up in the waitlist
    together with the escape room it belongs to
  * the sidebar exposes the waitlist, and the dashboard's pending tile links
    to it
  * approving / rejecting from the waitlist changes the reservation status and
    keeps the operator on the queue (the ``next`` field)
  * the admin panel renders dates in Jalali — timestamps through ``iso_dt``,
    booking dates through ``jdate``, schedule validity windows too

Every reservation, customer and history row created here is removed again, so
the developer's database is left exactly as found.
"""
from __future__ import annotations

import re
import sys
from datetime import timedelta

sys.path.insert(0, ".")

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))


from app import create_app
from app.db import execute, query, query_one
from app.jalali import PERSIAN_MONTHS, fa_digits

app = create_app()

from app.services import availability
from app.services.availability import create_reservation

TOK = "waitlist-token"


def _as_admin(client, admin_id: int) -> None:
    with client.session_transaction() as sess:
        sess["admin_id"] = admin_id
        sess["_csrf_token"] = TOK


def q(sql: str, args: tuple = ()):
    with app.app_context():
        return query(sql, args)


def q1(sql: str, args: tuple = ()):
    with app.app_context():
        return query_one(sql, args)


def ex(sql: str, args: tuple = ()):
    with app.app_context():
        return execute(sql, args)


super_id = q1("SELECT id FROM admins WHERE role='super' ORDER BY id LIMIT 1")["id"]
client = app.test_client()
_as_admin(client, super_id)


# ==========================================================================
# Fixtures: two pending requests in the future, on two different slots
# ==========================================================================
def _make_pending(suffix: str, days: int) -> tuple[int, int, int, str]:
    """Create a pending reservation; returns (reservation_id, customer_id,
    slot_id, code)."""
    with app.app_context():
        scenario = query_one("SELECT * FROM scenarios WHERE status='published' ORDER BY id LIMIT 1")
        when = availability.business_today() + timedelta(days=days)
        availability.ensure_slots(scenario["id"], when)
        slot = query_one(
            "SELECT * FROM slots WHERE scenario_id=? AND slot_date=? AND status='open' "
            "ORDER BY start_time LIMIT 1",
            (scenario["id"], when.isoformat()),
        )
        reservation = create_reservation(
            scenario_id=scenario["id"],
            slot_id=slot["id"],
            participants=2,
            name=f"تست لیست انتظار {suffix}",
            phone=f"0912000{('0' + suffix)[-4:]}",
        )
        row = query_one("SELECT id, customer_id FROM reservations WHERE code=?", (reservation["code"],))
        return row["id"], row["customer_id"], slot["id"], reservation["code"]


created: list[tuple[int, int]] = []  # (reservation_id, customer_id)

try:
    rid_ok, cid_ok, slot_ok, code_ok = _make_pending("1", 41)
    created.append((rid_ok, cid_ok))
    rid_no, cid_no, slot_no, code_no = _make_pending("2", 42)
    created.append((rid_no, cid_no))
    room_title = q1(
        "SELECT s.title AS t FROM reservations r JOIN scenarios s ON s.id=r.scenario_id WHERE r.id=?",
        (rid_ok,),
    )["t"]
    check("fixture reservations are pending",
          q1("SELECT status FROM reservations WHERE id=?", (rid_ok,))["status"] == "pending")

    # ----------------------------------------------------------------------
    # 1. The waitlist page itself
    # ----------------------------------------------------------------------
    page = client.get("/admin/bookings/waitlist")
    check("waitlist 200", page.status_code == 200, str(page.status_code))
    body = page.get_data(as_text=True)
    check("waitlist shows the booking code", code_ok in body, code_ok)
    check("waitlist shows the escape room name", room_title in body)
    check("waitlist offers approve", "تأیید" in body)
    check("waitlist offers reject", "رد" in body)
    check("waitlist posts back to itself", 'name="next" value="/admin/bookings/waitlist"' in body)
    alias = client.get("/admin/waitlist", follow_redirects=False)
    check("short /admin/waitlist alias redirects to the canonical path",
          alias.status_code in (301, 302)
          and alias.headers["Location"].endswith("/admin/bookings/waitlist"),
          f"{alias.status_code} {alias.headers.get('Location')}")

    # ----------------------------------------------------------------------
    # 2. Entry points: sidebar + dashboard tile
    # ----------------------------------------------------------------------
    check("sidebar links to the waitlist", 'href="/admin/bookings/waitlist"' in body)
    dash = client.get("/admin/").get_data(as_text=True)
    check("dashboard pending tile links to the waitlist",
          'class="stat-tile warn stat-tile-link" href="/admin/bookings/waitlist"' in dash)
    # The tile is the queue's entry point, so its number must be the size of the
    # queue — not just the requests whose slot is still ahead (a request that
    # was never decided does not stop needing a decision once its day passes).
    queue_size = q1(
        "SELECT COUNT(*) AS c FROM reservations "
        "WHERE status IN ('pending','awaiting_confirmation')"
    )["c"]
    tile = re.search(
        r'stat-tile warn stat-tile-link" href="/admin/bookings/waitlist">\s*'
        r'<span class="stat-tile-num">([^<]+)</span>',
        dash,
    )
    check("dashboard pending tile counts the whole queue",
          tile is not None and tile.group(1).strip() == str(queue_size),
          f"tile={tile.group(1).strip() if tile else None} queue={queue_size}")
    check("waitlist status pill is Persian",
          "در انتظار" in body and "PENDING" not in body)
    check("dashboard status pills are Persian (no raw enum)",
          "PENDING" not in dash and "در انتظار" in dash)
    bookings = client.get("/admin/bookings").get_data(as_text=True)
    check("bookings list links to the waitlist", 'href="/admin/bookings/waitlist"' in bookings)

    # ----------------------------------------------------------------------
    # 3. Approve straight from the queue
    # ----------------------------------------------------------------------
    resp = client.post(
        f"/admin/bookings/{rid_ok}/status",
        data={"status": "confirmed", "next": "/admin/bookings/waitlist", "_csrf_token": TOK},
        follow_redirects=False,
    )
    check("approve redirects back to the waitlist",
          resp.status_code in (301, 302) and resp.headers["Location"].endswith("/admin/bookings/waitlist"),
          f"{resp.status_code} {resp.headers.get('Location')}")
    check("approve sets status=confirmed",
          q1("SELECT status FROM reservations WHERE id=?", (rid_ok,))["status"] == "confirmed")
    after = client.get("/admin/bookings/waitlist").get_data(as_text=True)
    check("approved booking leaves the waitlist", code_ok not in after)

    # ----------------------------------------------------------------------
    # 4. Reject straight from the queue
    # ----------------------------------------------------------------------
    resp = client.post(
        f"/admin/bookings/{rid_no}/status",
        data={"status": "cancelled", "next": "/admin/bookings/waitlist", "_csrf_token": TOK},
        follow_redirects=False,
    )
    check("reject redirects back to the waitlist",
          resp.status_code in (301, 302) and resp.headers["Location"].endswith("/admin/bookings/waitlist"),
          f"{resp.status_code} {resp.headers.get('Location')}")
    check("reject sets status=cancelled",
          q1("SELECT status FROM reservations WHERE id=?", (rid_no,))["status"] == "cancelled")

    # An ordinary status change (no `next`) still lands on the detail page.
    plain = client.post(
        f"/admin/bookings/{rid_no}/status",
        data={"status": "pending", "_csrf_token": TOK},
        follow_redirects=False,
    )
    check("without next it falls back to the detail page",
          plain.headers["Location"].endswith(f"/admin/bookings/{rid_no}"),
          plain.headers.get("Location", ""))

    # An off-site `next` must not be honoured (no open redirect).
    evil = client.post(
        f"/admin/bookings/{rid_no}/status",
        data={"status": "cancelled", "next": "//evil.test/x", "_csrf_token": TOK},
        follow_redirects=False,
    )
    check("off-site next is ignored",
          "evil.test" not in evil.headers.get("Location", ""),
          evil.headers.get("Location", ""))

    # ----------------------------------------------------------------------
    # 5. Jalali dates
    # ----------------------------------------------------------------------
    with app.app_context():
        iso = app.jinja_env.filters["iso_dt"]
        jdate = app.jinja_env.filters["jdate"]
        rendered = iso("2026-01-15T10:30:00")
        check("iso_dt renders Jalali (Persian month + digits)",
              "دی" in rendered and fa_digits("1404") in rendered,
              rendered)
        # Stored timestamps are UTC (db.now_iso); the panel shows the shop's
        # clock, so 10:30 UTC is 14:00 in Tehran. Printing the raw value put
        # every timestamp in the panel 3.5 hours behind the hour it happened.
        check("iso_dt reads a stored UTC timestamp on the shop's clock",
              "۱۴:۰۰" in rendered, rendered)
        check("iso_dt has no Gregorian separators", "-" not in rendered, rendered)
        check("iso_dt keeps empty values empty", iso(None) == "" and iso("") == "")
        check("iso_dt passes unparsable values through", iso("not-a-date") == "not-a-date")
        check("jdate renders a booking date in Jalali", "مهر" in jdate("2025-10-04"), jdate("2025-10-04"))
        check("jdate survives None", jdate(None) == "")

    logs = client.get("/admin/logs").get_data(as_text=True)
    # Jalali month names, but Latin numerals: the panel prints numbers as
    # numbers, the public site keeps Persian digits.
    check("activity log dates are Jalali",
          any(m in logs for m in PERSIAN_MONTHS) and "140" in logs)
    dash_html = client.get("/admin/").get_data(as_text=True)
    check("dashboard dates are Jalali", any(m in dash_html for m in PERSIAN_MONTHS))

    # A schedule's validity window is shown in Jalali when it has one.
    sched = q1("SELECT id, valid_from, valid_to FROM schedules WHERE valid_from IS NOT NULL LIMIT 1")
    sched_html = client.get("/admin/schedules").get_data(as_text=True)
    if sched and sched["valid_from"]:
        from app.jalali import format_jalali
        from datetime import datetime as _dt

        expected = format_jalali(_dt.strptime(sched["valid_from"][:10], "%Y-%m-%d"))
        check("schedule validity window is Jalali", expected in sched_html, expected)
    else:
        check("schedule validity window (no dated row to check)", True)
    check("schedules page renders", "بازه‌های ثبت‌شده" in sched_html)

finally:
    # ------------------------------------------------------------------
    # Cleanup — remove every row this suite created.
    # ------------------------------------------------------------------
    for rid, cid in created:
        ex("DELETE FROM reservation_history WHERE reservation_id=?", (rid,))
        ex("DELETE FROM notifications WHERE reservation_id=?", (rid,))
        ex("DELETE FROM payment_transactions WHERE reservation_id=?", (rid,))
        ex("DELETE FROM reservations WHERE id=?", (rid,))
        left = q1("SELECT COUNT(*) AS c FROM reservations WHERE customer_id=?", (cid,))["c"]
        if not left:
            ex("DELETE FROM customers WHERE id=? AND name LIKE 'تست لیست انتظار%'", (cid,))
    leftovers = q1(
        "SELECT COUNT(*) AS c FROM reservations r JOIN customers c ON c.id=r.customer_id "
        "WHERE c.name LIKE 'تست لیست انتظار%'"
    )["c"]
    check("cleanup left no test reservations", leftovers == 0, str(leftovers))

failed = [x for x in RESULTS if not x[1]]
for name, ok, detail in RESULTS:
    print(("PASS  " if ok else "FAIL  ") + name + (("  <- " + detail) if (not ok and detail) else ""))
print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
sys.exit(1 if failed else 0)
