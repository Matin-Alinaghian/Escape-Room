"""Admin → booking management: list, filter, inspect, change status."""
from __future__ import annotations

from flask import abort, flash, redirect, render_template, request, url_for

from app.auth import current_admin, has_permission, require_permission
from app.blueprints.admin import bp
from app.db import query, query_one
from app.services.audit import log_action
from app.services.availability import (
    ALLOWED_TRANSITIONS,
    STATUS_LABELS,
    BookingError,
    business_today,
    change_status,
)


STATUSES = tuple(STATUS_LABELS)

ACTION_PERMISSION = {
    "cancelled": "bookings.cancel",
    "completed": "bookings.complete",
}


@bp.route("/bookings")
@require_permission("bookings.view")
def bookings_index():
    status = request.args.get("status", "")
    scenario_id = request.args.get("scenario", "")
    search = (request.args.get("q") or "").strip()
    where: list[str] = []
    args: list = []

    if status in STATUSES:
        where.append("r.status=?")
        args.append(status)
    if scenario_id.isdigit():
        where.append("r.scenario_id=?")
        args.append(int(scenario_id))
    if search:
        where.append("(r.code LIKE ? OR c.name LIKE ? OR c.phone LIKE ?)")
        args.extend([f"%{search}%"] * 3)

    clause = ("WHERE " + " AND ".join(where)) if where else ""
    rows = query(
        "SELECT r.*, s.title AS scenario_title, s.slug AS scenario_slug, "
        "c.name AS customer_name, c.phone AS customer_phone "
        f"FROM reservations r "
        "JOIN scenarios s ON s.id=r.scenario_id "
        "JOIN customers c ON c.id=r.customer_id "
        f"{clause} "
        "ORDER BY r.slot_date DESC, r.start_time DESC, r.id DESC LIMIT 300",
        tuple(args),
    )
    counts = {
        r["status"]: r["c"]
        for r in query(
            "SELECT status, COUNT(*) AS c FROM reservations GROUP BY status"
        )
    }
    return render_template(
        "admin/bookings/index.html",
        rows=rows,
        scenarios=query("SELECT id, title FROM scenarios ORDER BY title"),
        statuses=STATUSES,
        status_labels=STATUS_LABELS,
        counts=counts,
        filters={"status": status, "scenario": scenario_id, "q": search},
    )


@bp.route("/bookings/waitlist")
@require_permission("bookings.view")
def bookings_waitlist():
    """Requests waiting for a decision, grouped by nothing but time.

    Every public booking starts life as ``pending``; ``awaiting_confirmation``
    is the follow-up state for callers that walk the flow in two steps. Both
    belong on this queue, oldest slot first, so the room staff work through the
    day in order.
    """
    rows = query(
        "SELECT r.*, s.title AS scenario_title, s.slug AS scenario_slug, "
        "c.name AS customer_name, c.phone AS customer_phone "
        "FROM reservations r "
        "JOIN scenarios s ON s.id=r.scenario_id "
        "JOIN customers c ON c.id=r.customer_id "
        "WHERE r.status IN ('pending','awaiting_confirmation') "
        "ORDER BY r.slot_date ASC, r.start_time ASC, r.id ASC",
        (),
    )
    return render_template(
        "admin/bookings/waitlist.html",
        rows=rows,
        status_labels=STATUS_LABELS,
    )


@bp.route("/waitlist")
@require_permission("bookings.view")
def waitlist_alias():
    """Short URL for the queue — redirects so the sidebar stays highlighted.

    Two live URLs for one screen would leave the navigation link pointing at
    the nested path while the page was opened at the short one, so the alias
    bounces to the canonical location instead of rendering a second copy.
    """
    return redirect(url_for("admin.bookings_waitlist"))


@bp.route("/bookings/<int:reservation_id>")
@require_permission("bookings.view")
def bookings_detail(reservation_id: int):
    res = query_one(
        "SELECT r.*, s.title AS scenario_title, s.slug AS scenario_slug, "
        "c.name AS customer_name, c.phone AS customer_phone, c.email AS customer_email "
        "FROM reservations r JOIN scenarios s ON s.id=r.scenario_id "
        "JOIN customers c ON c.id=r.customer_id WHERE r.id=?",
        (reservation_id,),
    )
    if res is None:
        abort(404)
    history = query(
        "SELECT * FROM reservation_history WHERE reservation_id=? ORDER BY created_at",
        (reservation_id,),
    )
    payments = query(
        "SELECT * FROM payment_transactions WHERE reservation_id=? ORDER BY created_at",
        (reservation_id,),
    )
    notifications = query(
        "SELECT * FROM notifications WHERE reservation_id=? ORDER BY created_at",
        (reservation_id,),
    )
    review = query_one(
        "SELECT * FROM reviews WHERE reservation_id=?", (reservation_id,)
    )

    # Candidate slots for rescheduling: the next 21 days for this scenario.
    from app.services import availability

    target_days = request.args.get("days", "21")
    try:
        target_days = min(max(int(target_days), 1), 60)
    except ValueError:
        target_days = 21
    available_slots = availability.calendar_days(
        res["scenario_id"], business_today(), target_days
    )

    return render_template(
        "admin/bookings/detail.html",
        r=res,
        history=history,
        payments=payments,
        notifications=notifications,
        review=review,
        status_labels=STATUS_LABELS,
        next_states=sorted(ALLOWED_TRANSITIONS.get(res["status"], set())),
        available_days=available_slots,
        scenario_slots=query(
            "SELECT id, slot_date, start_time, end_time, capacity, status FROM slots "
            "WHERE scenario_id=? AND slot_date>=? ORDER BY slot_date, start_time LIMIT 200",
            (res["scenario_id"], business_today().isoformat()),
        ),
    )


@bp.route("/bookings/<int:reservation_id>/status", methods=["POST"])
@require_permission("bookings.edit")
def bookings_status(reservation_id: int):
    new = (request.form.get("status") or "").strip()
    note = (request.form.get("note") or "").strip()

    needed = ACTION_PERMISSION.get(new)
    if needed and not has_permission(needed):
        flash("برای این عملیات دسترسی لازم را ندارید.", "error")
        return redirect(_return_to(reservation_id))

    try:
        change_status(reservation_id, new, admin=current_admin(), note=note)
    except BookingError as exc:
        flash(str(exc), "error")
    else:
        log_action(
            f"reservation.{new}",
            f"#{reservation_id}{(' — ' + note) if note else ''}",
            entity_type="reservation",
            entity_id=reservation_id,
        )
        flash(f"وضعیت به «{STATUS_LABELS.get(new, new)}» تغییر کرد.", "success")
    return redirect(_return_to(reservation_id))


def _return_to(reservation_id: int) -> str:
    """Where to land after a status change.

    The waitlist screen posts a `next` field so approving or rejecting a
    request keeps the operator on the queue instead of bouncing them into the
    reservation they just handled. Only site-relative paths are honoured (a
    value like ``//evil.test`` is not), so this cannot become an open redirect.
    """
    candidate = (request.form.get("next") or "").strip()
    if candidate.startswith("/") and not candidate.startswith("//"):
        return candidate
    return url_for("admin.bookings_detail", reservation_id=reservation_id)


@bp.route("/bookings/<int:reservation_id>/note", methods=["POST"])
@require_permission("bookings.edit")
def bookings_note(reservation_id: int):
    note = (request.form.get("internal_note") or "").strip()
    from app.db import execute, now_iso

    execute(
        "UPDATE reservations SET internal_note=?, updated_at=? WHERE id=?",
        (note, now_iso(), reservation_id),
    )
    log_action(
        "reservation.note", f"#{reservation_id}", entity_type="reservation", entity_id=reservation_id
    )
    flash("یادداشت داخلی ذخیره شد.", "success")
    return redirect(url_for("admin.bookings_detail", reservation_id=reservation_id))


@bp.route("/bookings/<int:reservation_id>/reschedule", methods=["POST"])
@require_permission("bookings.edit")
def bookings_reschedule(reservation_id: int):
    """Move a reservation to another slot, capacity re-checked server side."""
    from app.db import execute, now_iso

    slot_id = request.form.get("slot_id")
    if not (slot_id or "").isdigit():
        flash("ساعت جدید انتخاب نشده است.", "error")
        return redirect(url_for("admin.bookings_detail", reservation_id=reservation_id))

    res = query_one("SELECT * FROM reservations WHERE id=?", (reservation_id,))
    slot = query_one("SELECT * FROM slots WHERE id=?", (int(slot_id),))
    if res is None or slot is None:
        abort(404)
    if slot["scenario_id"] != res["scenario_id"]:
        flash("ساعت انتخابی متعلق به این اتاق نیست.", "error")
        return redirect(url_for("admin.bookings_detail", reservation_id=reservation_id))

    from app.services.availability import OCCUPYING, seats_taken

    if res["status"] in OCCUPYING:
        taken = seats_taken(slot["id"])
        if taken + res["participants"] > slot["capacity"]:
            flash("ظرفیت آن ساعت کافی نیست.", "error")
            return redirect(url_for("admin.bookings_detail", reservation_id=reservation_id))

    execute(
        "UPDATE reservations SET slot_id=?, slot_date=?, start_time=?, updated_at=? WHERE id=?",
        (slot["id"], slot["slot_date"], slot["start_time"], now_iso(), reservation_id),
    )
    log_action(
        "reservation.reschedule",
        f"#{reservation_id} → {slot['slot_date']} {slot['start_time']}",
        entity_type="reservation",
        entity_id=reservation_id,
    )
    flash("رزرو به ساعت جدید منتقل شد.", "success")
    return redirect(url_for("admin.bookings_detail", reservation_id=reservation_id))