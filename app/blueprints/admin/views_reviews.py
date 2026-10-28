"""Admin → review moderation."""
from __future__ import annotations

from flask import abort, flash, redirect, render_template, request, url_for

from app.auth import current_admin, has_permission, require_permission
from app.blueprints.admin import bp
from app.db import query, query_one
from app.services.audit import log_action
from app.services.reviews import ReviewError, moderate

STATUSES = ("pending", "approved", "rejected", "hidden")
LABELS = {
    "pending": "در انتظار",
    "approved": "منتشر شده",
    "rejected": "رد شده",
    "hidden": "پنهان",
}


@bp.route("/reviews")
@require_permission("reviews.view")
def reviews_index():
    status = request.args.get("status", "")
    where, args = "", ()
    if status in STATUSES:
        where = "WHERE r.status=?"
        args = (status,)
    rows = query(
        "SELECT r.*, c.name AS customer_name, c.phone AS customer_phone, "
        "s.title AS scenario_title, s.slug AS scenario_slug "
        f"FROM reviews r JOIN customers c ON c.id=r.customer_id "
        f"JOIN scenarios s ON s.id=r.scenario_id {where} "
        "ORDER BY r.reported DESC, r.created_at DESC LIMIT 300",
        args,
    )
    counts = {
        r["status"]: r["c"]
        for r in query("SELECT status, COUNT(*) AS c FROM reviews GROUP BY status")
    }
    stats = query_one(
        "SELECT COUNT(*) AS c, AVG(rating) AS a FROM reviews WHERE status='approved'"
    )
    return render_template(
        "admin/reviews/index.html",
        rows=rows,
        statuses=STATUSES,
        labels=LABELS,
        counts=counts,
        active_status=status,
        average=round(float(stats["a"] or 0), 2),
        approved_count=int(stats["c"] or 0),
        can_moderate=has_permission("reviews.moderate"),
    )


@bp.route("/reviews/<int:review_id>/moderate", methods=["POST"])
@require_permission("reviews.moderate")
def reviews_moderate(review_id: int):
    action = (request.form.get("action") or "").strip()
    reply = (request.form.get("reply") or "").strip()
    try:
        moderate(review_id, action, current_admin() or {}, reply=reply)
    except ReviewError as exc:
        flash(str(exc), "error")
    else:
        flash("نظر به‌روزرسانی شد.", "success")
    return redirect(request.referrer or url_for("admin.reviews_index"))


@bp.route("/reviews/<int:review_id>", methods=["GET", "POST"])
@require_permission("reviews.view")
def reviews_detail(review_id: int):
    row = query_one(
        "SELECT r.*, c.name AS customer_name, s.title AS scenario_title "
        "FROM reviews r JOIN customers c ON c.id=r.customer_id "
        "JOIN scenarios s ON s.id=r.scenario_id WHERE r.id=?",
        (review_id,),
    )
    if row is None:
        abort(404)
    if request.method == "POST":
        if not has_permission("reviews.moderate"):
            flash("دسترسی لازم را ندارید.", "error")
        else:
            try:
                moderate(
                    review_id,
                    (request.form.get("action") or "").strip(),
                    current_admin() or {},
                    reply=(request.form.get("reply") or "").strip(),
                )
            except ReviewError as exc:
                flash(str(exc), "error")
            else:
                flash("نظر ذخیره شد.", "success")
                return redirect(url_for("admin.reviews_detail", review_id=review_id))
    return render_template("admin/reviews/detail.html", row=row, labels=LABELS)