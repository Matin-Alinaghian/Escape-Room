"""Admin → reports and CSV exports."""
from __future__ import annotations

import csv
import io
from datetime import timedelta

from flask import Response, render_template, request

from app.auth import require_permission
from app.blueprints.admin import bp
from app.db import query, query_one
from app.services.availability import business_today
from app.services.reviews import stats as review_stats


def _range() -> tuple[str, str, int]:
    days = request.args.get("days", "30")
    try:
        days = min(max(int(days), 1), 365)
    except ValueError:
        days = 30
    end = business_today() + timedelta(days=1)
    start = end - timedelta(days=days)
    return start.isoformat(), end.isoformat(), days


@bp.route("/reports")
@require_permission("reports.view")
def reports_index():
    start, end, days = _range()

    by_status = query(
        "SELECT status, COUNT(*) AS c, COALESCE(SUM(total_price),0) AS s "
        "FROM reservations WHERE created_at>=? AND created_at<? GROUP BY status",
        (start + "T00:00:00Z", end + "T00:00:00Z"),
    )
    by_scenario = query(
        "SELECT s.title, COUNT(r.id) AS c, COALESCE(SUM(r.total_price),0) AS s "
        "FROM reservations r JOIN scenarios s ON s.id=r.scenario_id "
        "WHERE r.slot_date>=? AND r.slot_date<? AND r.status NOT IN ('cancelled') "
        "GROUP BY s.id ORDER BY s DESC",
        (start, end),
    )
    daily = query(
        "SELECT slot_date AS d, COUNT(*) AS c, COALESCE(SUM(total_price),0) AS s "
        "FROM reservations WHERE slot_date>=? AND slot_date<? AND status NOT IN ('cancelled') "
        "GROUP BY slot_date ORDER BY slot_date",
        (start, end),
    )
    by_status_label = {
        "pending": "در انتظار",
        "awaiting_confirmation": "در انتظار تأیید",
        "confirmed": "تأیید شده",
        "awaiting_payment": "در انتظار پرداخت",
        "paid": "پرداخت شده",
        "completed": "تکمیل شده",
        "cancelled": "لغو شده",
        "no_show": "عدم حضور",
    }
    totals = query_one(
        "SELECT COUNT(*) AS c, COALESCE(SUM(total_price),0) AS s, "
        "COALESCE(SUM(participants),0) AS p FROM reservations "
        "WHERE slot_date>=? AND slot_date<? AND status NOT IN ('cancelled')",
        (start, end),
    )
    new_customers = query_one(
        "SELECT COUNT(*) AS c FROM customers WHERE created_at>=? AND created_at<?",
        (start + "T00:00:00Z", end + "T00:00:00Z"),
    )
    reviews = query_one(
        "SELECT COUNT(*) AS c FROM reviews WHERE created_at>=? AND created_at<?",
        (start + "T00:00:00Z", end + "T00:00:00Z"),
    )
    occupancy = query_one(
        "SELECT COALESCE(SUM(sl.capacity),0) AS cap FROM slots sl WHERE sl.slot_date>=? AND sl.slot_date<?",
        (start, end),
    )
    seats = int(totals["p"] or 0)
    cap = int(occupancy["cap"] or 0)

    return render_template(
        "admin/reports/index.html",
        days=days,
        start=start,
        end=end,
        by_status=[{**dict(r), "label": by_status_label.get(r["status"], r["status"])} for r in by_status],
        by_scenario=by_scenario,
        daily=daily,
        totals={
            "bookings": int(totals["c"] or 0),
            "revenue": int(totals["s"] or 0),
            "participants": seats,
            "capacity": cap,
            "occupancy": round(seats * 100 / cap, 1) if cap else 0.0,
            "avg_ticket": int(totals["s"] or 0) // max(int(totals["c"] or 0), 1),
        },
        new_customers=int(new_customers["c"] or 0),
        new_reviews=int(reviews["c"] or 0),
        review_stats=review_stats(),
        can_export=True,
    )


@bp.route("/reports/export/<dataset>")
@require_permission("export")
def reports_export(dataset: str):
    start, end, _ = _range()
    buffer = io.StringIO()

    if dataset == "reservations":
        buffer.write("﻿")  # BOM so Excel reads UTF-8 Persian correctly
        writer = csv.writer(buffer)
        writer.writerow(
            ["کد", "اتاق", "مشتری", "تلفن", "تاریخ", "ساعت", "نفرات", "مبلغ", "وضعیت", "ثبت"]
        )
        for r in query(
            "SELECT r.code, r.slot_date, r.start_time, r.participants, r.total_price, "
            "r.status, r.created_at, s.title AS scenario_title, "
            "c.name AS customer_name, c.phone AS customer_phone "
            "FROM reservations r JOIN scenarios s ON s.id=r.scenario_id "
            "JOIN customers c ON c.id=r.customer_id "
            "WHERE r.slot_date>=? AND r.slot_date<? ORDER BY r.slot_date, r.start_time",
            (start, end),
        ):
            writer.writerow(
                [
                    r["code"], r["scenario_title"], r["customer_name"], r["customer_phone"],
                    r["slot_date"], r["start_time"], r["participants"],
                    r["total_price"], r["status"], r["created_at"],
                ]
            )
    elif dataset == "customers":
        writer = csv.writer(buffer)
        buffer.write("﻿")
        writer.writerow(["نام", "تلفن", "ایمیل", "تاریخ عضویت"])
        for r in query("SELECT name, phone, email, created_at FROM customers ORDER BY created_at"):
            writer.writerow([r["name"], r["phone"], r["email"] or "", r["created_at"]])
    elif dataset == "reviews":
        writer = csv.writer(buffer)
        buffer.write("﻿")
        writer.writerow(["امتیاز", "عنوان", "متن", "وضعیت", "تاریخ"])
        for r in query("SELECT * FROM reviews ORDER BY created_at"):
            writer.writerow([r["rating"], r["title"], r["body"], r["status"], r["created_at"]])
    else:
        return render_template("errors/404.html"), 404

    return Response(
        buffer.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{dataset}-{end}.csv"'},
    )