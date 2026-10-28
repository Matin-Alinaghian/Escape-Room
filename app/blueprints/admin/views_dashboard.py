"""Admin dashboard: overview statistics, today's sessions and review queue."""
from __future__ import annotations

from datetime import date, timedelta

from flask import render_template, request

from app.auth import admin_permissions, current_admin, login_required
from app.blueprints.admin import bp
from app.db import query, query_one
from app.services import availability, reviews
from app.services.availability import STATUS_LABELS, business_today, business_now
from app.jalali import format_jalali

HORIZON = 60  # days

# Colour of the status pill next to a session (the admin stylesheet only knows
# the reservation statuses, so the session states borrow them).
STATE_PILLS = {
    "open": "confirmed",
    "booked": "pending",
    "full": "completed",
    "blocked": "cancelled",
    "past": "cancelled",
}


@bp.route("/")
@login_required
def dashboard():
    me = current_admin()
    perms = admin_permissions(me)
    today = business_today()
    iso_today = today.isoformat()
    horizon = (today + timedelta(days=HORIZON)).isoformat()
    tomorrow = (today + timedelta(days=1)).isoformat()

    def count(sql: str, args: tuple = ()) -> int:
        row = query_one(sql, args)
        return int(row["c"] or 0) if row else 0

    # The pending tile is the waitlist's entry point, so it must count exactly
    # what the queue shows — including requests whose slot has already passed
    # (they still need a decision, and the horizon window would hide them).
    pending = count(
        "SELECT COUNT(*) AS c FROM reservations "
        "WHERE status IN ('pending','awaiting_confirmation')"
    )
    confirmed = count(
        "SELECT COUNT(*) AS c FROM reservations WHERE status IN ('confirmed','paid','awaiting_payment') "
        "AND slot_date>=? AND slot_date<?",
        (iso_today, horizon),
    )
    today_total = count(
        "SELECT COUNT(*) AS c FROM reservations WHERE slot_date=? AND status NOT IN ('cancelled')",
        (iso_today,),
    )
    today_participants = count(
        "SELECT COALESCE(SUM(participants),0) AS c FROM reservations "
        "WHERE slot_date=? AND status NOT IN ('cancelled')",
        (iso_today,),
    )
    completed = count(
        "SELECT COUNT(*) AS c FROM reservations WHERE status='completed' AND completed_at>=?",
        (iso_today,),
    )
    cancelled = count(
        "SELECT COUNT(*) AS c FROM reservations WHERE status='cancelled' AND cancelled_at>=?",
        (iso_today,),
    )
    revenue_today = count(
        "SELECT COALESCE(SUM(total_price),0) AS c FROM reservations "
        "WHERE slot_date=? AND status IN ('confirmed','paid','completed')",
        (iso_today,),
    )
    revenue_month = count(
        "SELECT COALESCE(SUM(total_price),0) AS c FROM reservations "
        "WHERE slot_date>=? AND slot_date<? AND status IN ('confirmed','paid','completed')",
        (iso_today, (today + timedelta(days=30)).isoformat()),
    )

    # ---- rooms, and today's sessions one room at a time --------------------
    # Every room is listed (with what today looks like in it) and the chosen
    # room's sessions are shown underneath, so an administrator can close a
    # single room, close one session, and see at a glance which sessions are
    # already reserved.
    room_rows = query(
        "SELECT id, title, slug, status, is_bookable FROM scenarios "
        "WHERE status <> 'archived' ORDER BY sort_order, title"
    )
    active_room = (request.args.get("room") or "").strip()
    active_room_id = int(active_room) if active_room.isdigit() else None
    if active_room_id is not None and all(
        int(row["id"]) != active_room_id for row in room_rows
    ):
        active_room_id = None

    taken_sql = (
        "(SELECT COALESCE(SUM(participants),0) FROM reservations r "
        " WHERE r.slot_id=sl.id AND r.status NOT IN ('cancelled'))"
    )
    where = "sl.slot_date=?"
    args: tuple = (iso_today,)
    if active_room_id is not None:
        where += " AND sl.scenario_id=?"
        args = (iso_today, active_room_id)

    today_slots = []
    for row in query(
        "SELECT sl.*, s.title AS scenario_title, "
        f"{taken_sql} AS taken "
        "FROM slots sl JOIN scenarios s ON s.id=sl.scenario_id "
        f"WHERE {where} ORDER BY sl.start_time, s.title",
        args,
    ):
        taken = int(row["taken"] or 0)
        capacity = int(row["capacity"] or 0)
        remaining = max(capacity - taken, 0)
        if row["status"] != "open":
            state, label = "blocked", "بسته"
        elif remaining == 0:
            state, label = "full", "تکمیل"
        elif taken > 0:
            # A session a customer has already taken stays "رزرو شده" — it is
            # never offered as open again, not even to the administrator.
            state, label = "booked", "رزرو شده"
        elif availability.slot_time_state(row) == "past":
            state, label = "past", "گذشته"
        else:
            state, label = "open", "باز"
        today_slots.append(
            {
                **dict(row),
                "taken": taken,
                "remaining": remaining,
                "reserved": taken > 0,
                "state": state,
                "state_label": label,
                "pill": STATE_PILLS[state],
            }
        )

    # Per-room counts for the picker, computed over *all* rooms (the table above
    # may be filtered to one).
    per_room = {
        int(row["scenario_id"]): row
        for row in query(
            "SELECT sl.scenario_id, COUNT(*) AS total, "
            "SUM(CASE WHEN sl.status='open' THEN 1 ELSE 0 END) AS open_count, "
            f"SUM(CASE WHEN sl.status='open' AND {taken_sql} > 0 THEN 1 ELSE 0 END) AS booked "
            "FROM slots sl WHERE sl.slot_date=? GROUP BY sl.scenario_id",
            (iso_today,),
        )
    }
    rooms = []
    for row in room_rows:
        rid = int(row["id"])
        counts = per_room.get(rid)
        rooms.append(
            {
                "id": rid,
                "title": row["title"],
                "slug": row["slug"],
                "status": row["status"],
                "bookable": availability.room_bookable(row),
                "total_count": int(counts["total"] or 0) if counts else 0,
                "open_count": int(counts["open_count"] or 0) if counts else 0,
                "booked_count": int(counts["booked"] or 0) if counts else 0,
            }
        )
    active_room_row = next(
        (room for room in rooms if room["id"] == active_room_id), None
    )

    upcoming = query(
        "SELECT r.*, s.title AS scenario_title, s.slug AS scenario_slug, "
        "c.name AS customer_name, c.phone AS customer_phone "
        "FROM reservations r JOIN scenarios s ON s.id=r.scenario_id "
        "JOIN customers c ON c.id=r.customer_id "
        "WHERE r.status IN ('pending','awaiting_confirmation','confirmed','awaiting_payment','paid') "
        "AND r.slot_date>=? AND r.slot_date<? "
        "ORDER BY r.slot_date, r.start_time LIMIT 15",
        (iso_today, horizon),
    )

    recent_activity = query(
        "SELECT r.*, s.title AS scenario_title, c.name AS customer_name "
        "FROM reservations r JOIN scenarios s ON s.id=r.scenario_id "
        "JOIN customers c ON c.id=r.customer_id "
        "ORDER BY r.created_at DESC, r.id DESC LIMIT 12"
    )

    reviews_new = (
        reviews.list_reviews(status="pending", sort="newest", limit=8)
        if "reviews.view" in perms or me["role"] == "super"
        else []
    )
    reviews_latest = (
        reviews.list_reviews(status="approved", sort="newest", limit=6)
        if "reviews.view" in perms or me["role"] == "super"
        else []
    )

    stats_row = query_one(
        "SELECT COUNT(*) AS c, AVG(rating) AS a FROM reviews WHERE status='approved'"
    )

    # The clock in the header keeps ticking on its own (see admin.js): the
    # browser is given the server's instant and the two labels it may need, so
    # the panel can never sit there showing an hour that has long passed.
    now = business_now()

    return render_template(
        "admin/dashboard/index.html",
        rooms=rooms,
        active_room=active_room_id,
        active_room_row=active_room_row,
        can_schedule="schedules.manage" in perms or me["role"] == "super",
        dashboard_stats={
            "pending_count": pending,
            "confirmed_count": confirmed,
            "today_count": today_total,
            "today_participants": today_participants,
            "completed_count": completed,
            "cancelled_count": cancelled,
            "revenue_today": revenue_today,
            "revenue_month": revenue_month,
            "review_pending": len(reviews_new),
            "review_average": round(float(stats_row["a"] or 0), 2),
            "review_total": int(stats_row["c"] or 0),
        },
        today_label=format_jalali(today, with_weekday=True),
        tomorrow_label=format_jalali(today + timedelta(days=1), with_weekday=True),
        now_label=now.strftime("%H:%M"),
        now_iso=now.isoformat(),
        today_iso=today.isoformat(),
        today_slots=today_slots,
        upcoming=upcoming,
        recent=recent_activity,
        reviews_new=reviews_new,
        reviews_approved=reviews_latest,
        show_reviews="reviews.view" in perms or me["role"] == "super",
        horizon_days=HORIZON,
        # Persian labels for the status pills: the tables used to print the raw
        # enum value ("PENDING") in an otherwise all-Persian panel.
        status_labels=STATUS_LABELS,
    )