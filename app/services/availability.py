"""Availability engine and the concurrency-safe reservation transaction.

Concurrency model
-----------------
SQLite allows a single writer. Every booking runs inside ``BEGIN IMMEDIATE``
so concurrent requests serialise on the write lock; capacity is then
recomputed **inside** that transaction. The check is therefore server-side and
race-free: two customers can never take the same seat, and total participants
can never exceed ``slots.capacity``. Prices are always recomputed here —
values posted by the browser are only used to pick the slot, never to price it.
"""
from __future__ import annotations

import secrets
from datetime import date, datetime, timedelta, timezone

from app.db import execute, now_iso, query, query_one, write_txn

# Statuses that occupy capacity (only 'cancelled' releases a seat).
OCCUPYING = ("pending", "awaiting_confirmation", "confirmed", "awaiting_payment", "paid", "completed", "no_show")
CONFIRMED = ("confirmed", "paid", "completed")
COMPLETED = ("completed",)
CANCELLABLE = ("pending", "awaiting_confirmation", "confirmed", "awaiting_payment")

# Canonical Persian labels for a reservation status. Public and admin
# templates both render through this so the wording can never drift.
STATUS_LABELS: dict[str, str] = {
    "pending": "در انتظار",
    "awaiting_confirmation": "در انتظار تأیید",
    "confirmed": "تأیید شده",
    "awaiting_payment": "در انتظار پرداخت",
    "paid": "پرداخت شده",
    "cancelled": "لغو شده",
    "completed": "تکمیل شده",
    "no_show": "عدم حضور",
}


class BookingError(ValueError):
    """Business-rule violation with a Persian, user-presentable message."""

    def __init__(self, message: str, code: str = "invalid"):
        super().__init__(message)
        self.code = code


# --------------------------------------------------------------------------
# Time helpers
# --------------------------------------------------------------------------


_TZ_NAME = "Asia/Tehran"

# Iran stopped observing daylight saving in 2022, so Tehran is a constant
# +03:30. This table is only a safety net for hosts whose Python has no IANA
# database (a bare Windows install has none): without it the clock silently
# becomes UTC, which shifts every slot deadline by 3.5 hours.
_FIXED_OFFSETS = {"Asia/Tehran": timedelta(hours=3, minutes=30)}


def _business_tz():
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(_TZ_NAME)
    except Exception:
        offset = _FIXED_OFFSETS.get(_TZ_NAME)
        return timezone(offset, _TZ_NAME) if offset is not None else timezone.utc


def business_now() -> datetime:
    """Current time in the business timezone (falls back to UTC)."""
    return datetime.now(timezone.utc).astimezone(_business_tz())


def set_timezone(name: str) -> None:
    global _TZ_NAME
    _TZ_NAME = name or "Asia/Tehran"


def business_today() -> date:
    return business_now().date()


def to_business_time(dt: datetime) -> datetime:
    """A stored (UTC) timestamp, read on the shop's clock.

    Every ``created_at``/``updated_at`` in this schema is written in UTC
    (``db.now_iso`` and the column default), so printing it raw put every
    admin timestamp 3.5 hours behind the hour the office was actually in.
    Naive values are assumed to be UTC, which is how they are stored.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_business_tz())


def _minutes(t: str) -> int:
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def _hhmm(total: int) -> str:
    return f"{total // 60:02d}:{total % 60:02d}"


# --------------------------------------------------------------------------
# Slot generation
# --------------------------------------------------------------------------


def ensure_slots(scenario_id: int, when: date) -> None:
    """Materialise the booking slots for a date from the scenario schedules.

    A day that already holds every session its schedules describe is left
    alone; a day with sessions *missing* is topped up, so one leftover row
    (say, a single booked session from an older schedule) can never hide the
    rest of that day's availability. Re-running is harmless — the unique key
    plus ``INSERT OR IGNORE`` make it idempotent.
    """
    d = when.isoformat()
    wd = (when.weekday() + 2) % 7  # 0 = Saturday
    schedules = query(
        "SELECT * FROM schedules WHERE scenario_id=? AND is_active=1 "
        "AND (weekday IS NULL OR weekday=?) "
        "AND (valid_from IS NULL OR valid_from<=?) AND (valid_to IS NULL OR valid_to>=?)",
        (scenario_id, wd, d, d),
    )
    if not schedules:
        # Nothing opens this day; there is nothing to generate either.
        return

    scenario = query_one("SELECT * FROM scenarios WHERE id=?", (scenario_id,))
    if scenario is None:
        return

    plan: list[tuple[str, str, int, int]] = []
    for sched in schedules:
        step = int(sched["slot_minutes"])
        start, end = _minutes(sched["open_time"]), _minutes(sched["close_time"])
        if start >= end:
            continue
        price = sched["price_override"]
        if price is None:
            price = scenario["price_per_person"] or scenario["price_per_group"]
        cursor = start
        while cursor + step <= end:
            plan.append(
                (_hhmm(cursor), _hhmm(cursor + step), int(sched["capacity"]), int(price))
            )
            cursor += step

    if not plan:
        return
    already = query_one(
        "SELECT COUNT(*) AS c FROM slots WHERE scenario_id=? AND slot_date=?", (scenario_id, d)
    )
    if already and already["c"] >= len(plan):
        return

    for start_time, end_time, capacity, price in plan:
        execute(
            "INSERT OR IGNORE INTO slots "
            "(scenario_id, slot_date, start_time, end_time, capacity, price, status, generated) "
            "VALUES (?,?,?,?,?,?, 'open', 1)",
            (scenario_id, d, start_time, end_time, capacity, price),
        )


def ensure_range(scenario_id: int, start: date, days: int = 60) -> None:
    for offset in range(max(days, 0)):
        ensure_slots(scenario_id, start + timedelta(days=offset))


def seats_taken(slot_id: int, conn=None) -> int:
    sql = (
        "SELECT COALESCE(SUM(participants),0) AS s FROM reservations "
        "WHERE slot_id=? AND status IN (%s)" % ",".join("?" * len(OCCUPYING))
    )
    if conn is not None:
        row = conn.execute(sql, (slot_id, *OCCUPYING)).fetchone()
    else:
        row = query_one(sql, (slot_id, *OCCUPYING))
    return int(row["s"] or 0)


# Shown on every session of a room the administrator has switched off. The
# state stays ``blocked`` (that is what it is) but the wording tells the
# visitor *why* nothing can be picked.
ROOM_CLOSED_LABEL = "اتاق موقتاً بسته است"

# What a session looks like to a visitor. The label lives here, next to the
# capacity maths, so the server-rendered buttons and the JSON the booking page
# refreshes with can never disagree about what "reserved" means.
SLOT_STATE_LABELS: dict[str, str] = {
    "available": "ظرفیت آزاد",
    "sold_out": "رزرو شده",
    "blocked": "غیرفعال",
    # Time, not seats: the session has started, or starts too soon to be
    # arranged. Both read as "بooked نشدنی" on the booking page.
    "past": "گذشته",
    "too_late": "زمان کافی نمانده",
}

# The two unbookable-because-of-time states.
EXPIRED_STATES = ("past", "too_late")


def _notice_hours() -> int:
    """Minimum notice the shop requires, in hours (0 when it asks for none)."""
    from app.services.settings import get_setting

    try:
        return max(int(get_setting("booking.min_notice_hours", 0) or 0), 0)
    except (TypeError, ValueError):
        return 0


def slot_start(slot_row) -> datetime | None:
    """Aware start datetime of a slot, or ``None`` when it is unparsable."""
    try:
        naive = datetime.strptime(
            f"{slot_row['slot_date']} {slot_row['start_time']}", "%Y-%m-%d %H:%M"
        )
    except (TypeError, ValueError, KeyError):
        return None
    tz = business_now().tzinfo
    return naive.replace(tzinfo=tz) if tz else naive


def slot_time_state(slot_row, now: datetime | None = None) -> str:
    """``past`` / ``too_late`` / ``""`` (still bookable) for a slot's time.

    A visitor picking a day must never be offered a session that has already
    started, nor one inside the minimum-notice window — the server refuses
    both, so the button has to say so instead of failing after the submit.
    """
    start = slot_start(slot_row)
    if start is None:
        return ""
    now = now or business_now()
    if start <= now:
        return "past"
    if start < now + timedelta(hours=_notice_hours()):
        return "too_late"
    return ""


def slot_state(slot_row, taken: int | None = None) -> str:
    """One of ``available`` / ``sold_out`` / ``blocked`` / ``past`` / ``too_late``.

    A slot is *sold out* as soon as the seats of every reservation that still
    occupies capacity (``OCCUPYING`` — pending as well as confirmed) fill it,
    so confirming a booking turns the session red for everyone else. A session
    whose time has gone reads ``past``; one starting inside the minimum-notice
    window reads ``too_late``. A full session keeps saying so (that is the more
    useful fact about it), and either way it cannot be booked.
    """
    # ``status='sold_out'`` (written by the booking transaction) and a full
    # seat count mean the same thing; a status that is neither ``open`` nor
    # ``sold_out`` is something the administrator set (``blocked`` etc.).
    if slot_row["status"] == "sold_out":
        return "sold_out"
    if slot_row["status"] != "open":
        return "blocked"
    if taken is None:
        taken = seats_taken(slot_row["id"])
    if slot_row["capacity"] - taken <= 0:
        return "sold_out"
    return slot_time_state(slot_row) or "available"


def slot_detail(slot_row) -> dict:
    taken = seats_taken(slot_row["id"])
    remaining = max(slot_row["capacity"] - taken, 0)
    state = slot_state(slot_row, taken)
    return {
        "id": slot_row["id"],
        "start_time": slot_row["start_time"],
        "end_time": slot_row["end_time"],
        "capacity": slot_row["capacity"],
        "taken": taken,
        "remaining": remaining,
        "price": slot_row["price"],
        "status": slot_row["status"],
        "state": state,
        "state_label": SLOT_STATE_LABELS[state],
        "available": state == "available",
        "sold_out": state == "sold_out",
        "blocked": state == "blocked",
        "expired": state in EXPIRED_STATES,
    }


def room_bookable(scenario) -> bool:
    """Is the room open for booking at all (the admin's per-room switch)?

    A missing column — an older database that has not been migrated — reads as
    *open*, so the switch can never lock a room out by accident.
    """
    try:
        value = scenario["is_bookable"]
    except (IndexError, KeyError, TypeError):
        return True
    return value is None or int(value) != 0


def scenario_is_bookable(scenario_id: int) -> bool:
    row = query_one("SELECT * FROM scenarios WHERE id=?", (scenario_id,))
    return row is None or room_bookable(row)


def slots_for_date(scenario_id: int, when: date) -> list[dict]:
    ensure_slots(scenario_id, when)
    rows = query(
        "SELECT * FROM slots WHERE scenario_id=? AND slot_date=? ORDER BY start_time",
        (scenario_id, when.isoformat()),
    )
    details = [slot_detail(r) for r in rows]

    # A room switched off by the administrator advertises no session at all:
    # every card reads "اتاق موقتاً بسته است" and none of them is selectable,
    # on the server-rendered page and in the JSON alike.
    if not scenario_is_bookable(scenario_id):
        for detail in details:
            detail.update(
                state="blocked",
                state_label=ROOM_CLOSED_LABEL,
                available=False,
                sold_out=False,
                blocked=True,
                expired=False,
                room_closed=True,
            )
    else:
        for detail in details:
            detail["room_closed"] = False
    return details


def day_summary(scenario_id: int, when: date) -> dict:
    """Calendar cell data: whether the day has any bookable session.

    *Bookable* means a session that is still ahead of us and has seats left,
    so a day whose remaining slots have all started (or are inside the
    minimum-notice window) no longer advertises itself as free.
    """
    slots = slots_for_date(scenario_id, when)
    open_slots = [s for s in slots if s["status"] == "open"]
    bookable = [s for s in slots if s["state"] == "available"]
    return {
        "date": when.isoformat(),
        "total": len(slots),
        "open": len(open_slots),
        "available": len(bookable),
        "has_availability": bool(bookable),
        "is_past": when < business_today(),
    }


def calendar_days(scenario_id: int, start: date, days: int = 60) -> list[dict]:
    ensure_range(scenario_id, start, days)
    return [day_summary(scenario_id, start + timedelta(days=i)) for i in range(days)]


# --------------------------------------------------------------------------
# Pricing (server side only)
# --------------------------------------------------------------------------


def compute_price(scenario, slot_price: int, participants: int) -> tuple[int, int]:
    """Return (unit_price, total_price) in whole currency units."""
    if scenario["price_per_group"] and scenario["price_per_group"] > 0:
        unit = int(scenario["price_per_group"])
        total = unit
    else:
        unit = int(slot_price or scenario["price_per_person"] or 0)
        total = unit * participants
    discount = int(scenario["discount_percent"] or 0)
    if 0 < discount < 100:
        total = total * (100 - discount) // 100
    return unit, total


# --------------------------------------------------------------------------
# Reservation creation
# --------------------------------------------------------------------------


def _booking_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    while True:
        code = "ER-" + "".join(secrets.choice(alphabet) for _ in range(6))
        if query_one("SELECT id FROM reservations WHERE code=?", (code,)) is None:
            return code


def create_reservation(
    *,
    scenario_id: int,
    slot_id: int,
    participants: int,
    name: str,
    phone: str,
    first_name: str = "",
    family_name: str = "",
    note: str = "",
    email: str = "",
    source: str = "web",
    customer_id: int | None = None,
) -> dict:
    """Validate and persist a reservation request (server authoritative).

    ``customer_id`` is the account of a signed-in customer. When it is given
    the booking is attached to that account and the phone number posted by the
    browser is ignored; only when it is absent (guest/API callers) does the
    reservation fall back to looking the contact up by phone.
    """
    from app.jalali import en_digits

    try:
        # The count may arrive in Persian numerals (the visitor typed it, or
        # the form was submitted without JavaScript).
        participants = int(en_digits(str(participants).strip()))
    except (TypeError, ValueError):
        raise BookingError("تعداد نفرات نامعتبر است.")
    if participants < 1:
        raise BookingError("تعداد نفرات باید حداقل ۱ نفر باشد.")

    # Prefer the explicit first_name/family_name fields when provided,
    # otherwise split the single combined name field for backward
    # compatibility with callers that still pass a single name string.
    if not first_name and not family_name:
        parts = name.split()
        first_name = parts[0] if parts else ""
        family_name = " ".join(parts[1:]) if len(parts) > 1 else ""

    with write_txn() as conn:
        scenario = conn.execute("SELECT * FROM scenarios WHERE id=?", (scenario_id,)).fetchone()
        if scenario is None or scenario["status"] != "published":
            raise BookingError("این اتاق در دسترس نیست.", "scenario_unavailable")
        # The admin's per-room switch, enforced here (never only in the UI):
        # a closed room accepts no new reservation, whatever the browser posts.
        if not room_bookable(scenario):
            raise BookingError(
                "این اتاق موقتاً بسته است و رزرو نمی‌پذیرد.", "room_closed"
            )

        slot = conn.execute("SELECT * FROM slots WHERE id=? AND scenario_id=?", (slot_id, scenario_id)).fetchone()
        if slot is None:
            raise BookingError("ساعت انتخاب‌شده یافت نشد.", "slot_missing")
        if slot["status"] not in ("open", "sold_out"):
            # ``sold_out`` reaches here only through a stale browser tab; the
            # real message it deserves is about capacity, delivered below.
            raise BookingError("این ساعت بسته شده است.", "slot_blocked")

        slot_date = date.fromisoformat(slot["slot_date"])
        if slot_date < business_today():
            raise BookingError("این تاریخ گذشته است.", "past_date")
        # A session that already started is gone even when the shop asks for no
        # notice at all — the booking page greys it out, and the browser cannot
        # post its way around that.
        start_at = slot_start(slot)
        if start_at is not None and start_at <= business_now():
            raise BookingError("این ساعت گذشته است.", "past_slot")

        # Minimum notice window.
        from app.services.settings import get_setting

        notice = int(get_setting("booking.min_notice_hours", 0) or 0)
        if notice:
            try:
                start_dt = datetime.strptime(f"{slot['slot_date']} {slot['start_time']}", "%Y-%m-%d %H:%M")
                start_local = start_dt.replace(tzinfo=business_now().tzinfo)
                if start_local < business_now() + timedelta(hours=notice):
                    raise BookingError("برای این ساعت زمان کافی باقی نمانده است.", "too_late")
            except ValueError:
                pass

        if participants > scenario["max_players"]:
            raise BookingError(
                f"حداکثر ظرفیت این اتاق {scenario['max_players']} نفر است.", "capacity"
            )
        if participants < scenario["min_players"]:
            raise BookingError(
                f"حداقل تعداد نفرات برای این اتاق {scenario['min_players']} نفر است.", "min_players"
            )

        taken = int(seats_taken(slot_id, conn))
        capacity = int(slot["capacity"])
        slot_remaining = max(capacity - taken, 0)

        if taken + participants > capacity:
            raise BookingError(
                f"ظرفیت این ساعت تک کامل است (فقط {slot_remaining} نفر باقی مانده).",
                "sold_out",
            )

        # Once the last seat really is gone, mark the slot so every subsequent
        # view of the day (page render, calendar, API) reports "رزرو شده"
        # instead of keeping on re-selling the same remaining numbers.
        # (The slots table has no ``state`` column — ``status`` is the real
        # one — so an earlier ``state='sold_out'`` predicate could never
        # match anything and the marker was never written.)
        if taken + participants >= capacity and conn.execute(
            "SELECT 1 FROM slots WHERE id=? AND status='open'",
            (slot_id,),
        ).fetchone():
            conn.execute(
                "UPDATE slots SET status='sold_out' WHERE id=?",
                (slot_id,),
            )

        unit, total = compute_price(scenario, slot["price"], participants)

        # Store first_name / family_name separately so the admin can address the
        # customer by name in future SMS notifications. (If the columns have not
        # been migrated yet, fall back to the combined name field.)
        try:
            has_name_cols = bool(
                conn.execute(
                    "SELECT COUNT(*) AS c FROM pragma_table_info('customers') WHERE name=?", ("first_name",)
                ).fetchone()["c"]
            )
        except Exception:
            has_name_cols = False

        # Signed-in customer: the reservation belongs to their own account, so
        # the browser cannot choose whose booking this is.
        account = None
        if customer_id is not None:
            account = conn.execute(
                "SELECT * FROM customers WHERE id=?", (customer_id,)
            ).fetchone()
            if account is None:
                raise BookingError("حساب کاربری یافت نشد؛ دوباره وارد شوید.", "no_account")

        if account is not None:
            customer_id = account["id"]
            full_name = f"{first_name} {family_name}".strip() or name or account["name"]
            if has_name_cols:
                conn.execute(
                    "UPDATE customers SET name=?, first_name=?, family_name=?, "
                    "email=COALESCE(?, email), updated_at=? WHERE id=?",
                    (full_name, first_name, family_name, email or None, now_iso(), customer_id),
                )
            else:
                conn.execute(
                    "UPDATE customers SET name=?, email=COALESCE(?, email), updated_at=? "
                    "WHERE id=?",
                    (full_name, email or None, now_iso(), customer_id),
                )
        elif has_name_cols:
            full_name = f"{first_name} {family_name}".strip() or name
            cust = conn.execute("SELECT * FROM customers WHERE phone=?", (phone,)).fetchone()
            if cust is None:
                # `name` is NOT NULL in the schema, so always keep the combined
                # full name alongside the split columns.
                conn.execute(
                    "INSERT INTO customers(name, first_name, family_name, phone, email) VALUES (?,?,?,?,?)",
                    (full_name, first_name, family_name, phone, email or None),
                )
                customer_id = conn.execute("SELECT id FROM customers WHERE phone=?", (phone,)).fetchone()["id"]
            else:
                customer_id = cust["id"]
                conn.execute(
                    "UPDATE customers SET name=?, first_name=?, family_name=?, email=COALESCE(?, email), updated_at=? WHERE id=?",
                    (full_name, first_name, family_name, email or None, now_iso(), customer_id),
                )
        else:
            # Guest / API booking: reuse (or create) the contact by phone.
            cust = conn.execute("SELECT * FROM customers WHERE phone=?", (phone,)).fetchone()
            if cust is None:
                conn.execute(
                    "INSERT INTO customers(name, phone, email) VALUES (?,?,?)",
                    (name, phone, email or None),
                )
                customer_id = conn.execute("SELECT id FROM customers WHERE phone=?", (phone,)).fetchone()["id"]
            else:
                customer_id = cust["id"]
                conn.execute(
                    "UPDATE customers SET name=?, email=COALESCE(?, email), updated_at=? WHERE id=?",
                    (name, email or None, now_iso(), customer_id),
                )

        code = _booking_code()
        cur = conn.execute(
            "INSERT INTO reservations(code, scenario_id, slot_id, customer_id, participants, "
            "unit_price, total_price, status, source, customer_note, slot_date, start_time, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                code, scenario_id, slot_id, customer_id, participants,
                unit, total, "pending", source, note,
                slot["slot_date"], slot["start_time"], now_iso(), now_iso(),
            ),
        )
        reservation_id = cur.lastrowid
        conn.execute(
            "INSERT INTO reservation_history(reservation_id, from_status, to_status, actor, note, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (reservation_id, None, "pending", "customer", "ثبت درخواست رزرو", now_iso()),
        )

    from app.services.notifications import enqueue_event

    enqueue_event("booking.new", reservation_id)
    return get_reservation(code)


def get_reservation(code: str) -> dict | None:
    row = query_one(
        "SELECT r.*, s.title AS scenario_title, s.slug AS scenario_slug, s.cover_image, "
        "c.name AS customer_name, c.phone AS customer_phone "
        "FROM reservations r JOIN scenarios s ON s.id=r.scenario_id "
        "JOIN customers c ON c.id=r.customer_id WHERE r.code=?",
        (code,),
    )
    return dict(row) if row else None


# --------------------------------------------------------------------------
# Status transitions
# --------------------------------------------------------------------------

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"awaiting_confirmation", "confirmed", "cancelled"},
    "awaiting_confirmation": {"confirmed", "cancelled", "awaiting_payment"},
    "confirmed": {"completed", "cancelled", "no_show", "paid", "awaiting_payment"},
    "awaiting_payment": {"paid", "cancelled", "confirmed"},
    "paid": {"completed", "cancelled", "refunded"},
    "completed": set(),
    "cancelled": set(),
    "no_show": {"completed"},
}
ALLOWED_TRANSITIONS["paid"].discard("refunded")  # 'refunded' is not a reservation status


def change_status(
    reservation_id: int,
    new_status: str,
    *,
    admin: dict | None = None,
    note: str = "",
    notify: bool = True,
) -> dict:
    """Transition a reservation, recording history atomically."""
    if new_status not in ALLOWED_TRANSITIONS and new_status not in {
        "pending", "awaiting_confirmation", "confirmed", "awaiting_payment",
        "paid", "cancelled", "completed", "no_show",
    }:
        raise BookingError("وضعیت نامعتبر است.", "bad_status")

    with write_txn() as conn:
        row = conn.execute("SELECT * FROM reservations WHERE id=?", (reservation_id,)).fetchone()
        if row is None:
            raise BookingError("رزرو یافت نشد.", "not_found")
        current = row["status"]
        if new_status == current:
            return dict(row)
        allowed = ALLOWED_TRANSITIONS.get(current, set())
        if new_status not in allowed:
            raise BookingError(
                f"تغییر وضعیت از «{current}» به «{new_status}» مجاز نیست.", "bad_transition"
            )

        # Re-check capacity if a cancelled seat is being re-activated.
        if new_status in OCCUPYING and current == "cancelled":
            taken = seats_taken(row["slot_id"], conn)
            slot = conn.execute("SELECT capacity FROM slots WHERE id=?", (row["slot_id"],)).fetchone()
            if slot and taken + row["participants"] > slot["capacity"]:
                raise BookingError("ظرفیت آن ساعت دیگر خالی نیست.", "sold_out")

        now = now_iso()
        extra = {}
        if new_status == "confirmed":
            extra["confirmed_at"] = now
        elif new_status == "completed":
            extra["completed_at"] = now
        elif new_status == "cancelled":
            extra["cancelled_at"] = now
            extra["cancel_reason"] = note or None

        sets = ", ".join(["status=?", "updated_at=?"] + [f"{k}=?" for k in extra])
        conn.execute(
            f"UPDATE reservations SET {sets} WHERE id=?",
            (new_status, now, *extra.values(), reservation_id),
        )
        conn.execute(
            "INSERT INTO reservation_history(reservation_id, from_status, to_status, admin_id, actor, note, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                reservation_id, current, new_status,
                admin["id"] if admin else None,
                admin["username"] if admin else "system",
                note, now,
            ),
        )

    if notify:
        from app.services.notifications import enqueue_event

        mapping = {
            "confirmed": "booking.confirmed",
            "cancelled": "booking.cancelled",
            "completed": "booking.completed",
        }
        if new_status in mapping:
            enqueue_event(mapping[new_status], reservation_id)
        if new_status == "completed":
            from app.services.notifications import schedule_review_invite

            schedule_review_invite(reservation_id)
    return get_reservation_by_id(reservation_id)


def get_reservation_by_id(reservation_id: int) -> dict:
    row = query_one("SELECT code FROM reservations WHERE id=?", (reservation_id,))
    if row is None:
        raise BookingError("رزرو یافت نشد.", "not_found")
    return get_reservation(row["code"])
