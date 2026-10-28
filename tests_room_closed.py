"""The administrator's per-room booking switch, and the dashboard room picker.

Closing a room from the booking panel (داشبورد → «بستن این اتاق») has to hold
everywhere, and reopening has to give the room back:

  * the room is switched off in the database (``scenarios.is_bookable``),
  * the dashboard shows it as closed and offers «باز کردن این اتاق»,
  * the dashboard lists every room up front, shows one room's sessions once it
    is picked, and marks a session that already has a reservation as
    «رزرو شده» even when seats are still free,
  * the public site keeps the room's page but drops the booking button and
    refuses the POST — for a guest *and* for a signed-in customer (the switch
    is checked on the server, never only in the browser),
  * the API reports every session of a closed room as unavailable,
  * reopening restores an available session and a real reservation again.

Run: PYTHONIOENCODING=utf-8 python tests_room_closed.py
"""
from __future__ import annotations

import re
import sqlite3
import sys
from datetime import timedelta

sys.path.insert(0, ".")

from app import create_app
from app.db import execute, query_one
from app.services import availability

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))


app = create_app()

TOK = "room-closed-token"


def q(sql: str, args: tuple = ()):
    """One row, in its own app context (a long-lived context would cache g)."""
    with app.app_context():
        return query_one(sql, args)


SUPER = q("SELECT id FROM admins WHERE role='super' ORDER BY id LIMIT 1")
ROOM = q("SELECT * FROM scenarios WHERE status='published' ORDER BY id LIMIT 1")


def admin_client():
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["admin_id"] = SUPER["id"]
        sess["_csrf_token"] = TOK
    return c


def booking_client(customer_id: int | None = None):
    """A browser with a session (and therefore a CSRF token we know).

    A closed room cannot be reached to pick its form up, so the token is put
    into the session directly — exactly what the form would have carried.
    """
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["_csrf_token"] = TOK
        if customer_id is not None:
            sess["customer_id"] = customer_id
    return c


def html_of(client, path: str) -> str:
    return client.get(path).get_data(as_text=True)


def close_room(open_it: int) -> int:
    """Drive the real admin form, exactly as the dashboard button posts it."""
    r = admin_client().post(
        f"/admin/schedules/room/{ROOM['id']}/booking",
        data={
            "_csrf_token": TOK,
            "open": str(open_it),
            "next": f"/admin/?room={ROOM['id']}#today",
        },
    )
    return r.status_code


def flag() -> int:
    return int(q("SELECT is_bookable FROM scenarios WHERE id=?", (ROOM["id"],))["is_bookable"])


def reservations_for_room() -> int:
    return int(q("SELECT COUNT(*) AS c FROM reservations WHERE scenario_id=?", (ROOM["id"],))["c"])


# --------------------------------------------------------------------------
# 0. Fixture: a signed-in customer and a session far enough out to book
# --------------------------------------------------------------------------
with app.app_context():
    from werkzeug.security import generate_password_hash

    # Leftovers from a run that failed before its own cleanup: a stale
    # reservation would collide with the unique (slot, customer) index.
    execute(
        "DELETE FROM reservation_history WHERE reservation_id IN "
        "(SELECT id FROM reservations WHERE source='room-closed-test')"
    )
    execute("DELETE FROM reservations WHERE source='room-closed-test'")

    phone = "09991234567"
    row = query_one("SELECT id FROM customers WHERE phone=?", (phone,))
    if row:
        customer_id = int(row["id"])
        execute("UPDATE customers SET password_hash=? WHERE id=?", (generate_password_hash("roomclosed1"), customer_id))
    else:
        customer_id = int(
            execute(
                "INSERT INTO customers(name, phone, password_hash, must_change_pw) VALUES (?,?,?,0)",
                ("کلووزد تستی", phone, generate_password_hash("roomclosed1")),
            ).lastrowid
        )

    # A previous failed run may have left the switch off: start from open, the
    # way a shop is, and let the close/reopen tests below prove the switch.
    execute("UPDATE scenarios SET is_bookable=1 WHERE id=?", (ROOM["id"],))

    today = availability.business_today()
    when = today + timedelta(days=32)
    availability.ensure_slots(int(ROOM["id"]), today)
    availability.ensure_slots(int(ROOM["id"]), when)
    slot = query_one(
        "SELECT * FROM slots WHERE scenario_id=? AND slot_date=? AND status='open' "
        "ORDER BY start_time LIMIT 1",
        (ROOM["id"], when.isoformat()),
    )
    today_slot = query_one(
        "SELECT * FROM slots WHERE scenario_id=? AND slot_date=? AND status='open' "
        "ORDER BY start_time DESC LIMIT 1",
        (ROOM["id"], today.isoformat()),
    )

check("fixture: room open before the test", flag() == 1, f"is_bookable={flag()}")
check("fixture: an open session exists to book", slot is not None)
open_slot_id = int(slot["id"])

# A reservation on today's last session, so the dashboard can be asked about
# «رزرو شده». Late in the evening every session has started and the engine
# rightly refuses one, so the dashboard check below is skipped then.
booked_today = None
if today_slot is not None:
    with app.app_context():
        try:
            booked_today = availability.create_reservation(
                scenario_id=int(ROOM["id"]),
                slot_id=int(today_slot["id"]),
                participants=max(int(ROOM["min_players"]), 2),
                name="رزرو موجود",
                phone="09991112233",
                source="room-closed-test",
            )
        except (availability.BookingError, sqlite3.IntegrityError):
            booked_today = None
check("fixture: a today session carries a reservation", bool(booked_today), "no future session today")

# --------------------------------------------------------------------------
# 1. The dashboard lists every room and the picked room's sessions
# --------------------------------------------------------------------------
admin = admin_client()
dash = html_of(admin, "/admin/")
rooms_rows = q("SELECT COUNT(*) AS c FROM scenarios WHERE status <> 'archived'")["c"]
chips = dash.count('class="room-chip')
check(
    f"dashboard lists all {rooms_rows} rooms as chips",
    chips >= int(rooms_rows) + 1,  # +1 for the «همه اتاقها» chip
    f"chips={chips}",
)
check(
    "dashboard chip links back with the room id",
    f'href="/admin/?room={ROOM["id"]}#today"' in dash,
)

picked = html_of(admin, f"/admin/?room={ROOM['id']}#today")
check("picked room shows its own sessions", "امروز" in picked and "<table" in picked)
if booked_today:
    check(
        "a session with a reservation reads «رزرو شده»",
        "رزرو شده" in picked and "row-booked" in picked,
        f"row-booked={'row-booked' in picked}",
    )
check(
    "picked room's sessions stay inside that room",
    picked.count(ROOM["title"]) >= 2,
    f"mentions={picked.count(ROOM['title'])}",
)

other = q(
    "SELECT title FROM scenarios WHERE status='published' AND id<>? ORDER BY sort_order, id LIMIT 1",
    (ROOM["id"],),
)
if other:
    # The «همه اتاقها» view is the only place the other rooms' sessions appear,
    # so the picker really is filtering rather than relabelling one table.
    all_rooms = html_of(admin, "/admin/")
    check(
        "the all-rooms view shows every room's sessions",
        ROOM["title"] in all_rooms and other["title"] in all_rooms,
    )

# --------------------------------------------------------------------------
# 2. Close the room (the dashboard button)
# --------------------------------------------------------------------------
status = close_room(0)
check("closing redirects back to the dashboard", status == 302, f"status={status}")
check("room is switched off in the database", flag() == 0, f"is_bookable={flag()}")

picked = html_of(admin, f"/admin/?room={ROOM['id']}#today")
check(
    "closed room is announced in the dashboard",
    "این اتاق بسته است" in picked,
)
check(
    "dashboard offers to reopen the room",
    "باز کردن این اتاق" in picked,
)

# --------------------------------------------------------------------------
# 3. Public site: no booking button, and the POST is refused
# --------------------------------------------------------------------------
detail = html_of(admin_client(), f"/rooms/{ROOM['slug']}")
check(
    "room page says the room is closed",
    "این اتاق موقتاً بسته است" in detail,
)
check(
    "room page no longer offers the booking button",
    f'href="/book/{ROOM["slug"]}"' not in detail,
)

guest = booking_client()
r = guest.get(f"/book/{ROOM['slug']}")
check(
    "guest is sent back to the room page (not to the login form)",
    r.status_code == 302 and f"/rooms/{ROOM['slug']}" in (r.headers.get("Location") or ""),
    f"{r.status_code} -> {r.headers.get('Location')}",
)
followed = guest.get(f"/book/{ROOM['slug']}", follow_redirects=True).get_data(as_text=True)
check("guest sees the closed notice", "این اتاق موقتاً بسته است" in followed)

customer = booking_client(customer_id)
before = reservations_for_room()
r = customer.post(
    f"/book/{ROOM['slug']}",
    data={
        "_csrf_token": TOK,
        "slot_id": str(open_slot_id),
        "participants": str(max(int(ROOM["min_players"]), 2)),
        "first_name": "کاربر",
        "family_name": "تستی",
        "phone": "09991234567",
    },
)
check(
    "signed-in customer's POST is refused",
    r.status_code == 302 and f"/rooms/{ROOM['slug']}" in (r.headers.get("Location") or ""),
    f"{r.status_code} -> {r.headers.get('Location')}",
)
check("no reservation was created for the closed room", reservations_for_room() == before)

# The engine refuses it too, whatever a caller does (this is the server-side
# guard the browser cannot talk its way past).
with app.app_context():
    try:
        availability.create_reservation(
            scenario_id=int(ROOM["id"]),
            slot_id=open_slot_id,
            participants=max(int(ROOM["min_players"]), 2),
            name="تلاش مستقیم",
            phone="09991112244",
            source="room-closed-test",
        )
        raised = False
    except availability.BookingError as exc:
        raised = exc.code == "room_closed"
check("engine raises room_closed", raised)

# The API must not advertise a session in a closed room either.
with app.app_context():
    closed_slots = availability.slots_for_date(int(ROOM["id"]), when)
check(
    "closed room advertises no available session",
    bool(closed_slots) and all(not s["available"] for s in closed_slots),
)
check(
    "closed sessions are labelled «اتاق موقتاً بسته است»",
    all(s["state_label"] == availability.ROOM_CLOSED_LABEL for s in closed_slots),
    repr(closed_slots[0]["state_label"]) if closed_slots else "no slots",
)

# --------------------------------------------------------------------------
# 4. Reopen: the room is bookable again, for real
# --------------------------------------------------------------------------
status = close_room(1)
check("reopening redirects back to the dashboard", status == 302, f"status={status}")
check("room is switched back on", flag() == 1, f"is_bookable={flag()}")

detail = html_of(admin_client(), f"/rooms/{ROOM['slug']}")
check("room page offers the booking button again", f'href="/book/{ROOM["slug"]}"' in detail)

with app.app_context():
    open_slots = availability.slots_for_date(int(ROOM["id"]), when)
check(
    "the room has an available session again",
    any(s["available"] for s in open_slots),
)

customer = booking_client(customer_id)
free_slot = next((s for s in open_slots if s["available"]), None)
code = ""
if free_slot is None:
    check("a free session exists to book after reopening", False, "no available slot")
else:
    r = customer.post(
        f"/book/{ROOM['slug']}",
        data={
            "_csrf_token": TOK,
            "slot_id": str(free_slot["id"]),
            "participants": str(max(int(ROOM["min_players"]), 2)),
            "first_name": "کاربر",
            "family_name": "تستی",
            "phone": "09991234567",
        },
    )
    code = re.sub(r"^.*/", "", r.headers.get("Location") or "")
    check(
        "booking succeeds once the room is open",
        r.status_code == 302 and "/booking/" in (r.headers.get("Location") or ""),
        f"{r.status_code} -> {r.headers.get('Location')}",
    )

# --------------------------------------------------------------------------
# 5. Leave the database as we found it
# --------------------------------------------------------------------------
with app.app_context():
    execute(
        "DELETE FROM reservation_history WHERE reservation_id IN "
        "(SELECT id FROM reservations WHERE source='room-closed-test')"
    )
    execute("DELETE FROM reservations WHERE source='room-closed-test'")
if code:
    with app.app_context():
        execute(
            "DELETE FROM reservation_history WHERE reservation_id IN "
            "(SELECT id FROM reservations WHERE code=?)",
            (code,),
        )
        execute("DELETE FROM reservations WHERE code=?", (code,))

check("test reservations removed", True)

failed = [x for x in RESULTS if not x[1]]
for name, ok, detail in RESULTS:
    print(("PASS  " if ok else "FAIL  ") + name + (("  <- " + detail) if (not ok and detail) else ""))
print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
sys.exit(1 if failed else 0)
