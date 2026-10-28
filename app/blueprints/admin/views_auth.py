"""Admin authentication: login, logout, profile and password change."""
from __future__ import annotations

from flask import (
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from urllib.parse import urlparse

from app.auth import (
    change_password,
    current_admin,
    login_admin,
    login_required,
    logout_admin,
    verify_password,
)
from app.blueprints.admin import bp
from app.db import query_one
from app.security import client_ip, generate_csrf_token, limiter

_PASSWORD_MIN_LENGTH = 10


def _safe_next(target: str | None) -> str:
    """Only allow same-site relative redirects (never an absolute URL)."""
    if not target:
        return url_for("admin.dashboard")
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc:
        return url_for("admin.dashboard")
    if not target.startswith("/"):
        return url_for("admin.dashboard")
    return target


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_admin() is not None:
        return redirect(url_for("admin.dashboard"))

    error = ""
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password", "")

        # Throttle per IP *and* per username so one attacker cannot lock out
        # the legitimate operator by hammering a single account.
        ip = client_ip()
        if not limiter.allow("login-ip", ip, int(current_app.config.get("RATE_LIMIT_LOGIN_PER_MINUTE", 5))) or not limiter.allow(
            "login-user", username.lower(), int(current_app.config.get("RATE_LIMIT_LOGIN_PER_MINUTE", 5))
        ):
            error = "تلاش‌های ناموفق زیاد بود. چند دقیقه دیگر دوباره تلاش کنید."
        elif not username or not password:
            error = "نام کاربری و رمز عبور الزامی است."
        else:
            row = query_one(
                "SELECT id, username, display_name, password_hash, role, is_active, must_change_pw "
                "FROM admins WHERE username=?",
                (username,),
            )
            if row is None or not verify_password(row["password_hash"], password):
                error = "نام کاربری یا رمز عبور اشتباه است."
            elif not row["is_active"]:
                error = "اکانت این مدیر غیرفعال است."
            else:
                login_admin(row["id"], ip)
                from app.services.audit import log_action

                log_action(
                    "admin.login",
                    f"login as {row['username']}",
                    entity_type="admin",
                    entity_id=row["id"],
                )
                flash(f"خوش آمدید، {row['display_name']}.", "success")
                next_url = _safe_next(request.args.get("next"))
                if row["must_change_pw"]:
                    # Temporary password issued by the super admin: force a
                    # change before anything else in the panel.
                    flash("رمز عبور موقت است؛ لطفاً آن را از بخش پروفایل تغییر دهید.", "info")
                    next_url = url_for("admin.profile")
                return redirect(next_url)

    return render_template("admin/auth/login.html", error=error)


@bp.route("/logout", methods=["GET", "POST"])
@login_required
def logout():
    admin = current_admin()
    logout_admin(admin["id"] if admin else None)
    flash("خارج شدید. به امید دیدار!", "info")
    return redirect(url_for("admin.login"))


@bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    admin = current_admin()
    # Re-read the full row: current_admin() deliberately omits password_hash.
    row = query_one(
        "SELECT id, username, display_name, role, is_active, must_change_pw, password_hash, "
        "last_login_at, created_at FROM admins WHERE id=?",
        (admin["id"],),
    )
    if row is None:
        session.clear()
        return redirect(url_for("admin.login"))

    success = ""
    fail = ""
    if request.method == "POST":
        action = request.form.get("action")
        if action == "change-password":
            old = request.form.get("old_password", "")
            new_pw = request.form.get("new_password", "")
            conf_pw = request.form.get("confirm_password", "")
            if not verify_password(row["password_hash"], old):
                fail = "رمز عبور فعلی اشتباه است."
            elif len(new_pw) < _PASSWORD_MIN_LENGTH:
                fail = f"رمز عبور حداقل {_PASSWORD_MIN_LENGTH} کاراکتر باشد."
            elif new_pw != conf_pw:
                fail = "رمزهای عبور مطابقت ندارند."
            elif verify_password(row["password_hash"], new_pw):
                fail = "رمز عبور جدید باید با رمز فعلی متفاوت باشد."
            else:
                change_password(admin["id"], new_pw)
                from app.services.audit import log_action

                log_action("admin.password.change", "password changed", entity_type="admin", entity_id=admin["id"])
                success = "رمز عبور تغییر کرد."
                row = dict(row)
                row["must_change_pw"] = 0
        elif action == "change-username":
            current_username = (request.form.get("current_username") or "").strip()
            new_username = (request.form.get("new_username") or "").strip()
            if not verify_password(row["password_hash"], request.form.get("confirm_password", "")):
                fail = "برای تغییر نام کاربری، رمز عبور فعلی را درست وارد کنید."
            elif current_username.lower() != row["username"].lower():
                fail = "نام کاربری فعلی مطابقت ندارد."
            elif not new_username or len(new_username) < 3:
                fail = "نام کاربری جدید حداقل ۳ کاراکتر باشد."
            elif new_username.lower() == row["username"].lower():
                fail = "نام کاربری جدید باید با نام فعلی متفاوت باشد."
            elif query_one("SELECT id FROM admins WHERE username=? AND id<>?", (new_username, admin["id"])):
                fail = "این نام کاربری قبلاً ثبت شده است."
            else:
                from app.db import execute, now_iso

                execute(
                    "UPDATE admins SET username=?, updated_at=? WHERE id=?",
                    (new_username, now_iso(), admin["id"]),
                )
                from app.services.audit import log_action

                log_action("admin.username.change", f"{row['username']} -> {new_username}",
                           entity_type="admin", entity_id=admin["id"])
                success = "نام کاربری تغییر کرد."
                row = dict(row)
                row["username"] = new_username
        elif action == "change-name":
            display = (request.form.get("display_name") or "").strip()
            if not display:
                fail = "نام نمایشی نمی‌تواند خالی باشد."
            else:
                from app.db import execute, now_iso

                execute(
                    "UPDATE admins SET display_name=?, updated_at=? WHERE id=?",
                    (display[:80], now_iso(), admin["id"]),
                )
                session.pop("admin_id", None)
                log_action("admin.profile.update", display, entity_type="admin", entity_id=admin["id"])
                success = "نام نمایشی ذخیره شد."
                row = dict(row)
                row["display_name"] = display
        else:
            fail = "عملیات نامعتبر است."

    return render_template(
        "admin/auth/profile.html",
        current_admin=row,
        csrf=generate_csrf_token(),
        success=success,
        fail=fail,
    )