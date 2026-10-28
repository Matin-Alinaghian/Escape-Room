"""A session that is booked to capacity must read as «رزرو شده» to visitors and
must refuse further bookings — on the server-rendered page, in the JSON the
calendar refreshes with, and in the booking transaction itself.

Run: PYTHONIOENCODING=utf-8 python tests_slots_full.py
"""
from __future__ import annotations

import sys
from datetime import timedelta

sys.path.insert(0, ".")

from app import create_app
from app.db import execute, query, query_one
from app.services import availability

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))


app = create_app()
client = app.test_client()
_ctx = app.app_context()
_ctx.push()

TOK = "slots-full-token"
with client.session_transaction() as sess:
    sess["admin_id"] = 1
    sess["_csrf_token"] = TOK


def purge_probes() -> None:
    """Drop this suite's probe rows, including ones an aborted run left behind."""
    stale = [
        r["id"]
        for r in query(
            "SELECT id FROM reservations WHERE source='test' OR customer_id IN "
            "(SELECT id FROM customers WHERE phone LIKE '099900000%')"
        )
    ]
    for rid in stale:
        execute("DELETE FROM notifications WHERE reservation_id=?", (rid,))
        execute("DELETE FROM reservation_history WHERE reservation_id=?", (rid,))
        execute("DELETE FROM reservations WHERE id=?", (rid,))
    execute("DELETE FROM customers WHERE phone LIKE '099900000%'")


purge_probes()

scenario = query_one("SELECT * FROM scenarios WHERE status='published' ORDER BY id LIMIT 1")
assert scenario is not None, "no published scenario in the database"

# A far-away day so the run never disturbs today's real availability.
when = availability.business_today() + timedelta(days=30)
availability.ensure_slots(scenario["id"], when)
slot = query_one(
    "SELECT * FROM slots WHERE scenario_id=? AND slot_date=? AND status='open' "
    "ORDER BY start_time LIMIT 1",
    (scenario["id"], when.isoformat()),
)
assert slot is not None, "no generated slot for the probe day"

capacity = int(slot["capacity"])
per_booking = min(int(scenario["max_players"]), max(int(scenario["min_players"]), 2))

created: list[int] = []
phones = ["09990000001", "09990000002", "09990000003"]


def book(participants: int, phone: str) -> dict:
    """Create a reservation directly through the real booking engine."""
    r = availability.create_reservation(
        scenario_id=scenario["id"],
        slot_id=slot["id"],
        participants=participants,
        name="تست ظرفیت",
        phone=phone,
        first_name="تست",
        family_name="ظرفیت",
        source="test",
    )
    created.append(query_one("SELECT id FROM reservations WHERE code=?", (r["code"],))["id"])
    return r


# Fill the slot with reservations that all still occupy capacity (pending).
remaining = capacity
filled = 0
for phone in phones:
    if remaining <= 0:
        break
    take = min(per_booking, remaining)
    if take < int(scenario["min_players"]):
        break
    book(take, phone)
    remaining -= take
    filled += 1

check("slot was filled to capacity", remaining == 0, f"remaining={remaining} filled={filled}")

# A full slot is already "sold out" and refuses a new booking.
try:
    book(per_booking, "09990000009")
    check("full slot refuses a new booking", False, "booking unexpectedly succeeded")
except availability.BookingError as exc:
    check("full slot refuses a new booking", exc.code == "sold_out", f"code={exc.code}")

# ---- confirm the reservation(s): an approved session stays full ------------
res_id = created[0]
r = client.post(
    f"/admin/bookings/{res_id}/status",
    data={"_csrf_token": TOK, "status": "confirmed"},
    follow_redirects=False,
)
check("admin confirms the booking", r.status_code == 302, f"status={r.status_code}")
status = query_one("SELECT status FROM reservations WHERE id=?", (res_id,))["status"]
check("reservation is confirmed", status == "confirmed", f"status={status}")

# ---- what visitors see -----------------------------------------------------
detail = availability.slots_for_date(scenario["id"], when)
mine = [s for s in detail if s["id"] == slot["id"]][0]
check("slot state is sold_out", mine["state"] == "sold_out", repr(mine))
check("slot label reads رزرو شده", mine["state_label"] == "رزرو شده", repr(mine["state_label"]))
check("slot reports no availability", mine["available"] is False and mine["sold_out"] is True, "")

api = client.get(f"/api/scenarios/{scenario['slug']}/slots?date={when.isoformat()}")
check("JSON API 200", api.status_code == 200, f"status={api.status_code}")
payload = api.get_json()["slots"]
api_slot = [s for s in payload if s["id"] == slot["id"]][0]
check("JSON carries the reserved state", api_slot["state"] == "sold_out",
      repr(api_slot))
check("JSON exposes the Persian label", api_slot["state_label"] == "رزرو شده",
      repr(api_slot.get("state_label")))

# The booking page is account-only, so sign a customer in first.
customer_id = query_one("SELECT id FROM customers LIMIT 1")["id"]
with client.session_transaction() as sess:
    sess["customer_id"] = customer_id
    sess["_csrf_token"] = TOK

page = client.get(f"/book/{scenario['slug']}?date={when.isoformat()}")
check("booking page 200", page.status_code == 200, f"status={page.status_code}")
html = page.get_data(as_text=True)
check("booking page shows رزرو شده", "رزرو شده" in html, "")
check("reserved button is styled sold out", "is-sold_out" in html, "")
check("reserved button is disabled", "disabled" in html and "aria-disabled" in html, "")

# ---- public booking POST must be rejected too ------------------------------
post = client.post(
    f"/book/{scenario['slug']}",
    data={"_csrf_token": TOK, "date": when.isoformat(), "slot_id": str(slot["id"]),
          "participants": per_booking, "first_name": "تست", "family_name": "نهایی",
          "phone": "09990000009"},
    follow_redirects=False,
)
check("web booking on a full slot rejected", post.status_code == 200, f"status={post.status_code}")
check("web booking flashes the capacity error",
      "ظرفیت" in post.get_data(as_text=True), "")

# ---------------------------------------------------------------- clean up
for rid in created:
    execute("DELETE FROM notifications WHERE reservation_id=?", (rid,))
    execute("DELETE FROM reservation_history WHERE reservation_id=?", (rid,))
    execute("DELETE FROM reservations WHERE id=?", (rid,))
execute("DELETE FROM customers WHERE phone LIKE '099900000%'")
check("probe reservations removed",
      query_one("SELECT COUNT(*) AS c FROM reservations WHERE source='test'")["c"] == 0, "")

failed = [x for x in RESULTS if not x[1]]
for name, ok, detail in RESULTS:
    print(("PASS  " if ok else "FAIL  ") + name + (("  <- " + detail) if (not ok and detail) else ""))
print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
_ctx.pop()
sys.exit(1 if failed else 0)
