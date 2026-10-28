"""Admin → customers and administrator accounts (super-only management)."""
from __future__ import annotations

from flask import abort, flash, redirect, render_template, request, url_for

from app.auth import (
    change_password,
    current_admin,
    hash_password,
    login_required,
    super_admin_required,
)
from app.blueprints.admin import bp
from app.db import execute, now_iso, query, query_one
from app.permissions import ALL_PERMISSIONS, PERMISSIONS, grouped_permissions
from app.services.audit import log_action

PASSWORD_MIN = 10


# --------------------------------------------------------------------------
# Customers
# --------------------------------------------------------------------------


@bp.route("/customers")
@bp.route("/users")
@login_required
def customers_index():
    search = (request.args.get("q") or "").strip()
    args: tuple = ()
    clause = ""
    if search:
        clause = "WHERE name LIKE ? OR phone LIKE ? OR IFNULL(email,'') LIKE ?"
        args = (f"%{search}%", f"%{search}%", f"%{search}%")

    rows = query(
        "SELECT c.*, COUNT(r.id) AS bookings, "
        "COALESCE(SUM(CASE WHEN r.status NOT IN ('cancelled') THEN r.total_price END),0) AS spent, "
        "MAX(r.created_at) AS last_booking "
        f"FROM customers c LEFT JOIN reservations r ON r.customer_id=c.id {clause} "
        "GROUP BY c.id ORDER BY c.created_at DESC LIMIT 500",
        args,
    )
    return render_template("admin/users/index.html", rows=rows, search=search)


@bp.route("/customers/<int:customer_id>", methods=["GET", "POST"])
@login_required
def customers_detail(customer_id: int):
    row = query_one("SELECT * FROM customers WHERE id=?", (customer_id,))
    if row is None:
        abort(404)

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        phone = (request.form.get("phone") or "").strip()
        email = (request.form.get("email") or "").strip()
        notes = (request.form.get("notes") or "").strip()
        if not name or not phone:
            flash("نام و شماره تماس الزامی است.", "error")
        else:
            clash = query_one(
                "SELECT id FROM customers WHERE phone=? AND id<>?", (phone, customer_id)
            )
            if clash:
                flash("این شماره تماس برای مشتری دیگری ثبت شده است.", "error")
            else:
                execute(
                    "UPDATE customers SET name=?, phone=?, email=?, notes=?, updated_at=? WHERE id=?",
                    (name, phone, email or None, notes or None, now_iso(), customer_id),
                )
                log_action("customer.update", name, entity_type="customer", entity_id=customer_id)
                flash("اطلاعات مشتری ذخیره شد.", "success")
                return redirect(url_for("admin.customers_detail", customer_id=customer_id))

    reservations = query(
        "SELECT r.*, s.title AS scenario_title FROM reservations r "
        "JOIN scenarios s ON s.id=r.scenario_id "
        "WHERE r.customer_id=? ORDER BY r.slot_date DESC",
        (customer_id,),
    )
    reviews = query(
        "SELECT * FROM reviews WHERE customer_id=? ORDER BY created_at DESC",
        (customer_id,),
    )
    stats_row = query_one(
        "SELECT COUNT(*) AS c, COALESCE(SUM(total_price),0) AS s FROM reservations "
        "WHERE customer_id=? AND status NOT IN ('cancelled')",
        (customer_id,),
    )
    return render_template(
        "admin/users/detail.html",
        row=row,
        reservations=reservations,
        reviews=reviews,
        stats={"count": int(stats_row["c"] or 0), "spent": int(stats_row["s"] or 0)},
    )


# --------------------------------------------------------------------------
# Administrators (super only)
# --------------------------------------------------------------------------


@bp.route("/admins")
@super_admin_required
def admins_index():
    admins = query(
        "SELECT id, username, display_name, role, is_active, must_change_pw, "
        "last_login_at, created_at FROM admins ORDER BY role, username"
    )
    perms: dict[int, set[str]] = {}
    for r in query("SELECT admin_id, permission FROM admin_permissions"):
        perms.setdefault(r["admin_id"], set()).add(r["permission"])
    return render_template(
        "admin/users/admins.html",
        rows=[{**dict(a), "permissions": sorted(perms.get(a["id"], set()))} for a in admins],
        groups=grouped_permissions(),
        all_permissions=ALL_PERMISSIONS,
        permission_labels={k: v[0] for k, v in PERMISSIONS.items()},
    )


@bp.route("/admins", methods=["POST"])
@super_admin_required
def admins_create():
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""
    display = (request.form.get("display_name") or "").strip() or username

    if not username:
        flash("نام کاربری الزامی است.", "error")
    elif query_one("SELECT id FROM admins WHERE username=?", (username,)):
        flash("این نام کاربری قبلاً ثبت شده است.", "error")
    elif len(password) < PASSWORD_MIN:
        flash(f"رمز عبور حداقل {PASSWORD_MIN} کاراکتر باشد.", "error")
    else:
        granted = [
            p for p in request.form.getlist("permissions") if p in ALL_PERMISSIONS
        ]
        cur = execute(
            "INSERT INTO admins(username, password_hash, display_name, role, must_change_pw) "
            "VALUES (?,?,?, 'secondary', 1)",
            (username, hash_password(password), display),
        )
        admin_id = cur.lastrowid
        for perm in granted:
            execute(
                "INSERT OR IGNORE INTO admin_permissions(admin_id, permission, granted_by) "
                "VALUES (?,?,?)",
                (admin_id, perm, current_admin()["id"]),
            )
        log_action(
            "admin.create",
            f"{username} ({len(granted)} دسترسی)",
            entity_type="admin",
            entity_id=admin_id,
        )
        flash("مدیر ثانویه ساخته شد.", "success")
    return redirect(url_for("admin.admins_index"))


@bp.route("/admins/<int:admin_id>/permissions", methods=["POST"])
@super_admin_required
def admins_permissions(admin_id: int):
    row = query_one("SELECT * FROM admins WHERE id=?", (admin_id,))
    if row is None:
        abort(404)
    if row["role"] == "super":
        flash("دسترسی مدیر اصلی قابل تغییر نیست.", "error")
        return redirect(url_for("admin.admins_index"))

    granted = [p for p in request.form.getlist("permissions") if p in ALL_PERMISSIONS]
    execute("DELETE FROM admin_permissions WHERE admin_id=?", (admin_id,))
    for perm in granted:
        execute(
            "INSERT OR IGNORE INTO admin_permissions(admin_id, permission, granted_by) "
            "VALUES (?,?,?)",
            (admin_id, perm, current_admin()["id"]),
        )
    log_action(
        "admin.permissions",
        f"{row['username']} → {len(granted)}",
        entity_type="admin",
        entity_id=admin_id,
    )
    flash("دسترسی‌ها ذخیره شد.", "success")
    return redirect(url_for("admin.admins_index"))


@bp.route("/admins/<int:admin_id>/toggle", methods=["POST"])
@super_admin_required
def admins_toggle(admin_id: int):
    row = query_one("SELECT * FROM admins WHERE id=?", (admin_id,))
    if row is None:
        abort(404)
    if row["role"] == "super":
        flash("مدیر اصلی را نمی‌توان غیرفعال کرد.", "error")
    else:
        execute(
            "UPDATE admins SET is_active=?, updated_at=? WHERE id=?",
            (0 if row["is_active"] else 1, now_iso(), admin_id),
        )
        log_action("admin.toggle", row["username"], entity_type="admin", entity_id=admin_id)
        flash("وضعیت مدیر تغییر کرد.", "success")
    return redirect(url_for("admin.admins_index"))


@bp.route("/admins/<int:admin_id>/delete", methods=["POST"])
@super_admin_required
def admins_delete(admin_id: int):
    """Remove a secondary administrator for good.

    The super administrator's own account is never deletable (that is how the
    panel could lock itself out), and neither is your own — disable those with
    the toggle when they should simply lose access. Their permission rows go
    with them (``ON DELETE CASCADE``), so nothing dangles in the catalogue.
    """
    row = query_one("SELECT * FROM admins WHERE id=?", (admin_id,))
    if row is None:
        abort(404)
    if row["role"] == "super":
        flash("حساب مدیر اصلی حذف نمی‌شود؛ برای قطع دسترسی، سایر مدیران را حذف یا غیرفعال کنید.", "error")
    elif admin_id == (current_admin() or {}).get("id"):
        flash("حساب خودتان را نمی‌توانید حذف کنید.", "error")
    else:
        execute("DELETE FROM admin_permissions WHERE admin_id=?", (admin_id,))
        execute("DELETE FROM admins WHERE id=?", (admin_id,))
        log_action("admin.delete", row["username"], entity_type="admin", entity_id=admin_id)
        flash("مدیر حذف شد.", "success")
    return redirect(url_for("admin.admins_index"))


@bp.route("/admins/<int:admin_id>/reset-password", methods=["POST"])
@super_admin_required
def admins_reset_password(admin_id: int):
    row = query_one("SELECT * FROM admins WHERE id=?", (admin_id,))
    if row is None:
        abort(404)
    password = request.form.get("password") or ""
    if len(password) < PASSWORD_MIN:
        flash(f"رمز عبور حداقل {PASSWORD_MIN} کاراکتر باشد.", "error")
    else:
        change_password(admin_id, password)
        log_action("admin.reset_password", row["username"], entity_type="admin", entity_id=admin_id)
        flash("رمز عبور بازنشانی شد.", "success")
    return redirect(url_for("admin.admins_index"))