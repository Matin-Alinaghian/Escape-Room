"""Admin → audit log (super administrator only)."""
from __future__ import annotations

from flask import flash, redirect, render_template, request, url_for

from app.auth import super_admin_required
from app.blueprints.admin import bp
from app.db import execute, query, query_one

PER_PAGE = 100


@bp.route("/logs")
@super_admin_required
def logs_index():
    q = (request.args.get("q") or "").strip()
    action = (request.args.get("action") or "").strip()
    admin_id = request.args.get("admin", "").strip()

    where: list[str] = []
    args: list = []
    if q:
        where.append("(detail LIKE ? OR admin_name LIKE ?)")
        args.extend([f"%{q}%", f"%{q}%"])
    if action:
        where.append("action LIKE ?")
        args.append(f"{action}%")
    if admin_id.isdigit():
        where.append("admin_id=?")
        args.append(int(admin_id))
    clause = ("WHERE " + " AND ".join(where)) if where else ""

    try:
        page = max(int(request.args.get("page", 1)), 1)
    except ValueError:
        page = 1

    total = query_one(f"SELECT COUNT(*) AS c FROM audit_logs {clause}", tuple(args))
    total_count = int(total["c"] or 0)
    rows = query(
        f"SELECT * FROM audit_logs {clause} ORDER BY created_at DESC, id DESC "
        "LIMIT ? OFFSET ?",
        (*args, PER_PAGE, (page - 1) * PER_PAGE),
    )
    actions = [
        r["a"] for r in query("SELECT DISTINCT action AS a FROM audit_logs ORDER BY a")
    ]
    admins = query(
        "SELECT DISTINCT admin_id, admin_name FROM audit_logs WHERE admin_id IS NOT NULL"
    )
    return render_template(
        "admin/logs/index.html",
        rows=rows,
        total=total_count,
        page=page,
        per_page=PER_PAGE,
        pages=max((total_count + PER_PAGE - 1) // PER_PAGE, 1),
        actions=actions,
        admins=admins,
        filters={"q": q, "action": action, "admin": admin_id},
    )


@bp.route("/logs/<int:log_id>/delete", methods=["POST"])
@super_admin_required
def logs_delete(log_id: int):
    """The audit trail is normally immutable; deletion is an explicit override."""
    reason = (request.form.get("reason") or "").strip()
    if len(reason) < 5:
        flash("برای حذف گزارش باید دلیل خود را بنویسید.", "error")
        return redirect(url_for("admin.logs_index"))
    row = query_one("SELECT * FROM audit_logs WHERE id=?", (log_id,))
    if row is None:
        flash("گزارش یافت نشد.", "error")
        return redirect(url_for("admin.logs_index"))
    execute("DELETE FROM audit_logs WHERE id=?", (log_id,))
    # The deletion is itself recorded, so the trail stays honest.
    from app.services.audit import log_action

    log_action(
        "audit.delete",
        f"log #{log_id} — {row['action']} — دلیل: {reason}",
        entity_type="audit_log",
        entity_id=log_id,
    )
    flash("گزارش حذف و حذف آن ثبت شد.", "success")
    return redirect(url_for("admin.logs_index"))