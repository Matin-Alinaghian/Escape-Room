"""Public JSON API used by the calendar, booking form and review widgets."""
from __future__ import annotations

from datetime import date, timedelta

from flask import Blueprint, jsonify, request

from app.db import query_one
from app.services import availability
from app.services.customer_auth import current_customer
from app.services.settings import get_setting

bp = Blueprint("api", __name__)


def _scenario(slug: str):
    row = query_one("SELECT * FROM scenarios WHERE slug=?", (slug,))
    if row is None or row["status"] != "published":
        return None
    return row


@bp.get("/health")
def health():
    from flask import current_app

    return jsonify(
        {
            "status": "ok",
            "sms": bool(current_app.config.get("SMS_ENABLED")),
            "payment": bool(current_app.config.get("PAYMENT_ENABLED")),
        }
    )


@bp.get("/scenarios/<slug>/calendar")
def calendar(slug: str):
    scenario = _scenario(slug)
    if scenario is None:
        return jsonify({"error": "not_found"}), 404
    try:
        days = min(max(int(request.args.get("days", 45)), 1), 120)
    except ValueError:
        days = 45
    start_raw = request.args.get("start")
    try:
        start = date.fromisoformat(start_raw) if start_raw else availability.business_today()
    except ValueError:
        start = availability.business_today()
    data = availability.calendar_days(scenario["id"], start, days)
    return jsonify({"start": start.isoformat(), "days": data})


@bp.get("/scenarios/<slug>/slots")
def slots(slug: str):
    scenario = _scenario(slug)
    if scenario is None:
        return jsonify({"error": "not_found"}), 404
    raw = request.args.get("date")
    try:
        when = date.fromisoformat(raw) if raw else availability.business_today()
    except ValueError:
        return jsonify({"error": "bad_date"}), 400
    # The label travels with the data: the booking page changes the day without
    # a reload, and only the server knows the Jalali wording for it.
    from app.jalali import format_jalali

    return jsonify(
        {
            "date": when.isoformat(),
            "date_label": format_jalali(when, with_weekday=True),
            "slots": availability.slots_for_date(scenario["id"], when),
        }
    )


@bp.post("/bookings")
def create_booking():
    if not get_setting("booking.enabled", True):
        return jsonify({"error": "booking_disabled"}), 403
    # Booking is account-only, same rule as the web form at /book/<slug>.
    customer = current_customer()
    if customer is None:
        return (
            jsonify(
                {
                    "error": "login_required",
                    "message": "برای رزرو آنلاین ابتدا وارد حساب کاربری خود شوید.",
                    "login_url": "/login",
                }
            ),
            401,
        )
    payload = request.get_json(silent=True) or {}
    try:
        reservation = availability.create_reservation(
            scenario_id=int(payload.get("scenario_id") or 0),
            slot_id=int(payload.get("slot_id") or 0),
            participants=payload.get("participants", 1),
            name=str(payload.get("name") or "").strip(),
            first_name=str(payload.get("first_name") or "").strip(),
            family_name=str(payload.get("family_name") or "").strip(),
            phone=str(payload.get("phone") or "").strip(),
            email=str(payload.get("email") or "").strip(),
            note=str(payload.get("note") or "").strip(),
            customer_id=customer["id"],
        )
    except availability.BookingError as exc:
        return jsonify({"error": exc.code, "message": str(exc)}), 400
    except (TypeError, ValueError):
        return jsonify({"error": "invalid_request", "message": "درخواست نامعتبر است."}), 400
    return (
        jsonify(
            {"ok": True, "code": reservation["code"], "url": f"/booking/{reservation['code']}"}
        ),
        201,
    )


@bp.get("/scenarios/<slug>/reviews")
def scenario_reviews(slug: str):
    from app.services.reviews import list_reviews, stats

    scenario = _scenario(slug)
    if scenario is None:
        return jsonify({"error": "not_found"}), 404
    sort = request.args.get("sort", "newest")
    try:
        limit = min(max(int(request.args.get("limit", 10)), 1), 50)
    except ValueError:
        limit = 10
    return jsonify(
        {
            "stats": stats(scenario["id"]),
            "reviews": list_reviews(scenario_id=scenario["id"], sort=sort, limit=limit),
        }
    )


@bp.get("/reviews/stats")
def review_stats():
    from app.services.reviews import stats

    sid = request.args.get("scenario_id")
    return jsonify(stats(int(sid)) if sid else stats())


@bp.post("/reviews/<int:review_id>/report")
def report_review(review_id: int):
    from app.services.reviews import report

    payload = request.get_json(silent=True) or {}
    report(review_id, str(payload.get("reason") or "نا مناسب"))
    return jsonify({"ok": True})

