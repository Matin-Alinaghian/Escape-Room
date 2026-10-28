"""Admin → schedules (weekly opening windows) and slot maintenance."""
from __future__ import annotations

from datetime import timedelta

from flask import abort, flash, redirect, render_template, request, url_for

from app.auth import require_permission
from app.blueprints.admin import bp
from app.db import execute, now_iso, query, query_one
from app.jalali import PERSIAN_WEEKDAYS
from app.services import availability
from app.services.audit import log_action

WEEKDAYS = PERSIAN_WEEKDAYS  # index 0 = Saturday


def _valid_time(value: str) -> bool:
    try:
        h, m = value.split(":")
        return 0 <= int(h) <= 23 and 0 <= int(m) <= 59
    except (ValueError, AttributeError):
        return False


def _slot_payload() -> dict:
    def num(name, default, low, high):
        raw = (request.form.get(name) or "").strip()
        raw = raw.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
        try:
            v = int(raw) if raw else default
        except ValueError:
            raise ValueError(f"مقدار «{name}» باید عدد باشد.") from None
        if not low <= v <= high:
            raise ValueError(f"مقدار «{name}» باید بین {low} و {high} باشد.")
        return v

    scenario_id = request.form.get("scenario_id", "")
    if not scenario_id.isdigit():
        raise ValueError("اتاق را انتخاب کنید.")
    open_time = (request.form.get("open_time") or "").strip()
    close_time = (request.form.get("close_time") or "").strip()
    if not _valid_time(open_time) or not _valid_time(close_time):
        raise ValueError("ساعت شروع و پایان باید به شکل HH:MM باشد.")
    if open_time >= close_time:
        raise ValueError("ساعت پایان باید بعد از ساعت شروع باشد.")

    weekday_raw = (request.form.get("weekday") or "").strip()
    weekday = None
    if weekday_raw != "":
        try:
            weekday = int(weekday_raw)
        except ValueError:
            raise ValueError("روز هفته نامعتبر است.") from None
        if not 0 <= weekday <= 6:
            raise ValueError("روز هفته نامعتبر است.")

    price_raw = (request.form.get("price_override") or "").strip()
    price = None
    if price_raw:
        price = num("price_override", 0, 0, 100_000_000)

    return {
        "scenario_id": int(scenario_id),
        "weekday": weekday,
        "open_time": open_time,
        "close_time": close_time,
        "slot_minutes": num("slot_minutes", 60, 15, 480),
        "capacity": num("capacity", 6, 1, 60),
        "price_override": price,
        "valid_from": (request.form.get("valid_from") or "").strip() or None,
        "valid_to": (request.form.get("valid_to") or "").strip() or None,
        "is_active": 1 if request.form.get("is_active") else 0,
    }


@bp.route("/schedules")
@require_permission("schedules.manage")
def schedules_index():
    scenario_id = request.args.get("scenario", "")
    where, args = "", ()
    if scenario_id.isdigit():
        where = "WHERE s.scenario_id=?"
        args = (int(scenario_id),)
    rows = query(
        "SELECT s.*, sc.title AS scenario_title FROM schedules s "
        f"JOIN scenarios sc ON sc.id=s.scenario_id {where} "
        "ORDER BY s.scenario_id, s.weekday, s.open_time",
        args,
    )
    return render_template(
        "admin/schedules/index.html",
        rows=rows,
        weekdays=WEEKDAYS,
        scenarios=query("SELECT id, title FROM scenarios ORDER BY title"),
        active_scenario=scenario_id,
    )


@bp.route("/schedules", methods=["POST"])
@require_permission("schedules.manage")
def schedules_create():
    try:
        data = _slot_payload()
    except ValueError as exc:
        flash(str(exc), "error")
    else:
        cols = ",".join(data)
        marks = ",".join("?" * len(data))
        execute(f"INSERT INTO schedules({cols}) VALUES ({marks})", tuple(data.values()))
        log_action("schedule.create", f"scenario #{data['scenario_id']}", entity_type="schedule")
        flash("بازه زمانی اضافه شد.", "success")
    return redirect(url_for("admin.schedules_index", scenario=request.form.get("scenario_id", "")))


@bp.route("/schedules/<int:schedule_id>/update", methods=["POST"])
@require_permission("schedules.manage")
def schedules_update(schedule_id: int):
    existing = query_one("SELECT * FROM schedules WHERE id=?", (schedule_id,))
    if existing is None:
        abort(404)
    try:
        data = _slot_payload()
    except ValueError as exc:
        flash(str(exc), "error")
    else:
        sets = ", ".join(f"{k}=?" for k in data)
        execute(
            f"UPDATE schedules SET {sets} WHERE id=?",
            (*data.values(), schedule_id),
        )
        # Existing future slots must follow the new rules, so drop them and
        # let them be regenerated from the schedule on next calendar request.
        execute(
            "DELETE FROM slots WHERE scenario_id=? AND slot_date>=? AND generated=1",
            (data["scenario_id"], availability.business_today().isoformat()),
        )
        log_action("schedule.update", f"schedule #{schedule_id}", entity_type="schedule")
        flash("بازه زمانی به‌روزرسانی شد و ساعات آینده بازسازی می‌شوند.", "success")
    return redirect(url_for("admin.schedules_index", scenario=existing["scenario_id"]))


@bp.route("/schedules/<int:schedule_id>/delete", methods=["POST"])
@require_permission("schedules.manage")
def schedules_delete(schedule_id: int):
    row = query_one("SELECT * FROM schedules WHERE id=?", (schedule_id,))
    if row is None:
        abort(404)
    execute("DELETE FROM schedules WHERE id=?", (schedule_id,))
    log_action("schedule.delete", f"schedule #{schedule_id}", entity_type="schedule")
    flash("بازه زمانی حذف شد.", "success")
    return redirect(url_for("admin.schedules_index", scenario=row["scenario_id"]))


@bp.route("/schedules/<int:schedule_id>/toggle", methods=["POST"])
@require_permission("schedules.manage")
def schedules_toggle(schedule_id: int):
    row = query_one("SELECT * FROM schedules WHERE id=?", (schedule_id,))
    if row is None:
        abort(404)
    execute(
        "UPDATE schedules SET is_active=? WHERE id=?",
        (0 if row["is_active"] else 1, schedule_id),
    )
    flash("وضعیت بازه زمانی تغییر کرد.", "success")
    return redirect(url_for("admin.schedules_index", scenario=row["scenario_id"]))


def _return_to(fallback: str) -> str:
    """Where to land after an action, honouring a site-relative `next`.

    The dashboard posts `next` so closing a session keeps the operator on the
    room they were looking at. Only site-relative paths are honoured (``//x``
    is not), so this can never become an open redirect.
    """
    candidate = (request.form.get("next") or "").strip()
    if candidate.startswith("/") and not candidate.startswith("//"):
        return candidate
    return fallback


@bp.route("/slots/block", methods=["POST"])
@require_permission("schedules.manage")
def slots_block():
    """Open or block a single materialised slot."""
    slot_id = request.form.get("slot_id")
    fallback = request.referrer or url_for("admin.schedules_index")
    if not (slot_id or "").isdigit():
        flash("ساعت انتخاب نشده است.", "error")
        return redirect(_return_to(fallback))
    slot = query_one("SELECT * FROM slots WHERE id=?", (int(slot_id),))
    if slot is None:
        abort(404)

    if slot["status"] not in ("open", "sold_out"):
        # A session that already carries a reservation is never offered as open
        # again: reopening it would put the reserved seats back on sale. It can
        # still be closed (the customers keep their booking either way).
        taken = availability.seats_taken(slot["id"])
        if taken > 0:
            flash(
                f"این ساعت {taken} نفر رزرو دارد؛ تا زمانی که رزرو آن پابرجاست باز نمی‌شود.",
                "error",
            )
            return redirect(_return_to(fallback))
        new = "open"
    else:
        new = "blocked"

    execute(
        "UPDATE slots SET status=?, updated_at=? WHERE id=?",
        (new, now_iso(), slot["id"]),
    )
    log_action("slot.status", f"#{slot['id']} → {new}", entity_type="slot", entity_id=slot["id"])
    flash("این ساعت باز شد." if new == "open" else "این ساعت بسته شد.", "success")
    return redirect(_return_to(url_for("admin.schedules_index", scenario=slot["scenario_id"])))


@bp.route("/schedules/room/<int:scenario_id>/booking", methods=["POST"])
@require_permission("schedules.manage")
def schedules_room_booking(scenario_id: int):
    """Close or reopen a whole room for booking (the admin's room switch).

    Closing is not hiding: the room keeps its page, its photos, its reviews and
    its history — it just takes no new reservation, and the booking page says
    so instead of offering times that would be refused.
    """
    scenario = query_one("SELECT * FROM scenarios WHERE id=?", (scenario_id,))
    if scenario is None:
        abort(404)
    open_it = (request.form.get("open") or "").strip()
    bookable = 1 if open_it in ("1", "true", "on") else 0
    execute(
        "UPDATE scenarios SET is_bookable=?, updated_at=? WHERE id=?",
        (bookable, now_iso(), scenario_id),
    )
    log_action(
        "scenario.booking.open" if bookable else "scenario.booking.close",
        scenario["title"],
        entity_type="scenario",
        entity_id=scenario_id,
    )
    flash(
        "این اتاق برای رزرو باز شد." if bookable else "این اتاق بسته شد؛ رزرو جدیدی ثبت نمی‌شود.",
        "success",
    )
    return redirect(_return_to(url_for("admin.dashboard")))


@bp.route("/slots/regenerate", methods=["POST"])
@require_permission("schedules.manage")
def slots_regenerate():
    days = request.form.get("days", "60")
    try:
        days = min(max(int(days), 1), 180)
    except ValueError:
        days = 60
    scenario_id = request.form.get("scenario_id", "")
    scenarios = (
        [int(scenario_id)] if scenario_id.isdigit()
        else [r["id"] for r in query("SELECT id FROM scenarios WHERE status='published'")]
    )
    start = availability.business_today()
    for sid in scenarios:
        availability.ensure_range(sid, start, days)
        execute(
            "DELETE FROM slots WHERE scenario_id=? AND slot_date<? AND generated=1",
            (sid, start.isoformat()),
        )
    log_action("slot.regenerate", f"{days} روز — {len(scenarios)} اتاق", entity_type="slot")
    flash(f"ساعات {days} روز آینده بازسازی شد.", "success")
    return redirect(url_for("admin.schedules_index", scenario=scenario_id))