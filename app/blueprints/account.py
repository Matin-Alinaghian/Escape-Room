"""Public account area: unified login (customer + admin), forgot password.

The user asked for ONE login page with an icon where both the customer
(booking/purchase) and the administrator (management) can sign in — the two
forms live on the same page as two tabs sharing one visual card.

Security notes
--------------
* All POSTs are CSRF-protected (global before_request hook).
* Login attempts are throttled per IP and per identifier using the same
  sliding-window limiter as the admin panel.
* Forgot-password never reveals whether a phone number exists.
* Customer and admin sessions use different session keys and cannot coexist
  harmfully: logging into one clears the other.
"""
from __future__ import annotations

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from app.auth import current_admin
from app.security import client_ip, limiter
from app.services.customer_auth import (
    CustomerAuthError,
    cancel_signup,
    change_password,
    complete_signup,
    current_customer,
    forgot_password,
    is_valid_phone,
    login as customer_login,
    logout_customer,
    mask_phone,
    normalize_phone,
    signup_code_verified,
    signup_demo_code,
    signup_phone,
    start_signup,
    verify_signup_code,
)

bp = Blueprint("account", __name__)


def _safe_next(value) -> str:
    """A post-sign-in destination is honoured only when it is a local path.

    Anything absolute (``https://evil.example``), protocol-relative
    (``//evil.example``) or empty is dropped, so the login form can never be
    used as an open redirect.
    """
    value = (value or "").strip()
    if value.startswith("/") and not value.startswith("//"):
        return value
    return ""


def _too_many() -> str | None:
    if limiter.allow("login-ip", client_ip(), int(current_app.config.get("RATE_LIMIT_LOGIN_PER_MINUTE", 5))):
        return None
    return "تلاش‌های ناموفق زیاد بود. چند دقیقه دیگر دوباره تلاش کنید."


def _limit(bucket: str, key: str, limit: int) -> bool:
    """Sliding-window check; ``True`` while the caller is still under *limit*."""
    return limiter.allow(bucket, key, limit)


@bp.route("/login", methods=["GET", "POST"])
def unified_login():
    """One login card, one form.

    A single identifier field accepts either a customer phone number (09...)
    or an admin username. Admins are checked first, then customers, so the
    admin signs in from the same box and lands in the admin panel.
    """
    # Where the visitor wanted to go before signing in (e.g. the booking page
    # they were sent here from). It is kept in the session as well, so it
    # survives the sign-up wizard; only local paths are accepted.
    nxt = _safe_next(request.args.get("next")) or _safe_next(session.get("auth_next"))
    if nxt:
        session["auth_next"] = nxt

    if request.method == "POST":
        identifier = (request.form.get("identifier") or "").strip()
        password = request.form.get("password", "")
        limited = _too_many()
        if limited:
            flash(limited, "error")
            return redirect(url_for("account.unified_login"))

        # ---- try admin sign-in first (has no phone-number shape) ----
        if not limiter.allow("login-user", identifier.lower(), int(current_app.config.get("RATE_LIMIT_LOGIN_PER_MINUTE", 5))):
            flash("تلاش‌های ناموفق زیاد بود. چند دقیقه دیگر دوباره تلاش کنید.", "error")
            return redirect(url_for("account.unified_login"))
        from app.db import query_one

        from app.auth import login_admin, verify_password

        row = query_one(
            "SELECT id, username, display_name, password_hash, role, is_active, must_change_pw "
            "FROM admins WHERE username=?",
            (identifier,),
        )
        if row is not None:
            if not verify_password(row["password_hash"], password):
                flash("نام کاربری/شماره یا رمز عبور اشتباه است.", "error")
                return redirect(url_for("account.unified_login"))
            if not row["is_active"]:
                flash("اکانت این مدیر غیرفعال است.", "error")
                return redirect(url_for("account.unified_login"))
            from app.services.audit import log_action

            ip = client_ip()
            login_admin(row["id"], ip)
            log_action("admin.login", f"login as {row['username']}",
                       entity_type="admin", entity_id=row["id"])
            flash(f"خوش آمدید، {row['display_name']}.", "success")
            if row["must_change_pw"]:
                flash("رمز عبور موقت است؛ لطفاً آن را از بخش پروفایل تغییر دهید.", "info")
                return redirect(url_for("admin.profile"))
            return redirect(url_for("admin.dashboard"))

        # ---- customer sign-in ----
        # A phone number that has no account is not a "wrong password" — the
        # visitor belongs in the sign-up wizard, so send them there directly
        # (this is the first-time path the owner asked for).
        phone_shaped = normalize_phone(identifier)
        if is_valid_phone(phone_shaped):
            existing = query_one(
                "SELECT id, password_hash FROM customers WHERE phone=?", (phone_shaped,)
            )
            if existing is None or not existing["password_hash"]:
                flash("این شماره ثبت‌نام نشده است؛ برای ساخت حساب، کد تأیید را دریافت کنید.", "info")
                return redirect(url_for("account.register", phone=phone_shaped))
        try:
            customer = customer_login(identifier, password)
        except CustomerAuthError as exc:
            flash(str(exc), "error")
            return redirect(url_for("account.unified_login"))
        flash(f"خوش آمدید، {customer['name']}.", "success")
        if customer["must_change_pw"]:
            flash("رمز فعلی موقت است؛ لطفاً آن را تغییر دهید.", "info")
            return redirect(url_for("account.account"))
        return redirect(nxt or url_for("account.account"))

    # GET: render the unified login card. ``?static=1`` is used by the static
    # export so the mirrored page explains that it cannot sign anyone in.
    return render_template("public/login.html", static_mode=request.args.get("static") == "1")


@bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password_page():
    if request.method == "POST":
        phone = request.form.get("identifier") or request.form.get("phone", "")
        ok, message = forgot_password(phone)
        flash(message, "success" if ok else "error")
        return redirect(url_for("account.unified_login"))
    return render_template("public/forgot_password.html")


# ---------------------------------------------------------------------------
# Sign-up: number -> SMS code -> password
# ---------------------------------------------------------------------------
#
# Three pages, each one a real server-side step: the second cannot be reached
# with a phone number that never received a code, and the third cannot be
# reached until that code was accepted. All three are CSRF-protected by the
# global before_request hook like every other form on the site.


@bp.route("/register", methods=["GET", "POST"])
def register():
    """Step 1 — the phone number the verification code is sent to."""
    nxt = _safe_next(request.args.get("next"))
    if nxt:
        session["auth_next"] = nxt
    if request.method == "POST":
        if not _limit("signup-ip", client_ip(), 8):
            flash("درخواست‌های ثبت‌نام از این دستگاه زیاد بود؛ چند دقیقه دیگر تلاش کنید.", "error")
            return redirect(url_for("account.register"))
        raw_phone = request.form.get("phone") or request.form.get("identifier", "")
        phone = normalize_phone(raw_phone)
        per_minute = int(current_app.config.get("RATE_LIMIT_LOGIN_PER_MINUTE", 5))
        if not _limit("signup-phone", phone or "-", per_minute):
            flash("برای این شماره درخواست‌های زیادی ثبت شد؛ کمی صبر کنید.", "error")
            return redirect(url_for("account.register", phone=phone))
        if signup_phone() and signup_phone() != phone:
            cancel_signup()  # a corrected number: drop the previous attempt
        try:
            ok, message = start_signup(raw_phone)
        except CustomerAuthError as exc:
            flash(str(exc), "error")
            return redirect(url_for("account.register", phone=phone))
        flash(message, "success" if ok else "error")
        if not ok:
            return redirect(url_for("account.register", phone=phone))
        return redirect(url_for("account.register_code"))

    prefill = normalize_phone(request.args.get("phone", ""))
    return render_template(
        "public/register.html",
        step="phone",
        phone=prefill,
        pending_phone=signup_phone(),
    )


@bp.route("/register/code", methods=["GET", "POST"])
def register_code():
    """Step 2 — the six-digit code that was texted to the number."""
    phone = signup_phone()
    if not phone:
        flash("ابتدا شماره تلفن خود را وارد کنید.", "info")
        return redirect(url_for("account.register"))

    if request.method == "POST":
        action = request.form.get("action", "verify")
        if action == "change":
            cancel_signup()
            return redirect(url_for("account.register"))
        if action == "resend":
            if not _limit("signup-phone", phone, int(current_app.config.get("RATE_LIMIT_LOGIN_PER_MINUTE", 5))):
                flash("درخواست کد زیاد بود؛ کمی صبر کنید.", "error")
                return redirect(url_for("account.register_code"))
            try:
                ok, message = start_signup(phone)
            except CustomerAuthError as exc:
                flash(str(exc), "error")
                return redirect(url_for("account.register_code"))
            flash(message, "success" if ok else "error")
            return redirect(url_for("account.register_code"))
        try:
            verify_signup_code(request.form.get("code", ""))
        except CustomerAuthError as exc:
            flash(str(exc), "error")
            return redirect(url_for("account.register_code"))
        return redirect(url_for("account.register_password"))

    return render_template(
        "public/register.html",
        step="code",
        phone=phone,
        masked_phone=mask_phone(phone),
        demo_code=signup_demo_code(),
    )


@bp.route("/register/password", methods=["GET", "POST"])
def register_password():
    """Step 3 — name + password; the account is created and signed in."""
    phone = signup_phone()
    if not phone:
        return redirect(url_for("account.register"))
    if not signup_code_verified():
        flash("برای تعیین رمز عبور ابتدا کد تأیید را وارد کنید.", "info")
        return redirect(url_for("account.register_code"))

    form = {"name": ""}
    if request.method == "POST":
        # Read the destination *before* the sign-up, because creating the
        # account rotates the session (and with it this stash).
        nxt = _safe_next(session.get("auth_next"))
        try:
            customer = complete_signup(
                request.form.get("name", ""),
                request.form.get("password", ""),
                request.form.get("confirm", ""),
            )
        except CustomerAuthError as exc:
            flash(str(exc), "error")
            form["name"] = (request.form.get("name") or "").strip()
            return render_template(
                "public/register.html", step="password", phone=phone, form=form
            )
        flash(f"خوش آمدید، {customer['name']}!", "success")
        return redirect(nxt or url_for("account.account"))

    return render_template("public/register.html", step="password", phone=phone, form=form)


@bp.route("/account", methods=["GET", "POST"])
def account():
    customer = current_customer()
    if customer is None:
        return redirect(url_for("account.unified_login"))

    if request.method == "POST":
        action = request.form.get("action", "change-password")
        if action == "logout":
            logout_customer()
            flash("خارج شدید. به امید دیدار!", "info")
            return redirect(url_for("public.home"))
        try:
            if action == "change-password":
                change_password(
                    customer["id"],
                    request.form.get("old_password", ""),
                    request.form.get("new_password", ""),
                )
                flash("رمز عبور تغییر کرد.", "success")
        except CustomerAuthError as exc:
            flash(str(exc), "error")
        # refresh
        customer = current_customer()

    from app.db import query

    reservations = query(
        "SELECT r.*, s.title AS scenario_title FROM reservations r "
        "JOIN scenarios s ON s.id=r.scenario_id "
        "WHERE r.customer_id=? ORDER BY r.slot_date DESC LIMIT 20",
        (customer["id"],),
    )
    return render_template(
        "public/account.html",
        customer=customer,
        reservations=reservations,
    )
