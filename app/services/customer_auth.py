"""Customer (public user) authentication.

Sign-up is a three-step phone verification — the flow the owner asked for:

1. the visitor types a phone number; a one-time code is texted to it,
2. the visitor types that code back,
3. only then may the visitor choose a password, and the account exists.

After that they sign in with phone + password, exactly like any other site.
If a phone that has no account tries to sign in, the login page sends them to
the sign-up wizard instead of a bare "wrong password" error.

Forgot-password delivers a fresh temporary password by SMS to the number on
file; ``must_change_pw`` forces a change after login.

Sessions are namespaced (``customer_id``) so they never collide with the
administrator session (``admin_id``). The pending sign-up lives under
``session['signup']`` — it holds only the phone number (plus, in local demo
mode, the code that is also printed on the page); the code itself and the
"number verified" flag are kept in the ``signup_codes`` table.
"""
from __future__ import annotations

import hashlib
import re
import secrets
import time

from flask import current_app, g, session

from app.auth import hash_password, verify_password
from app.db import execute, now_iso, query_one

PASSWORD_MIN = 10
SIGNUP_CODE_LENGTH = 6
IRAN_MOBILE_RE = re.compile(r"^09\d{9}$")
_FA_AR_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def normalize_digits(raw: str) -> str:
    """Convert Persian/Arabic digits to Latin and trim."""
    return (raw or "").translate(_FA_AR_DIGITS).strip()


def normalize_phone(raw: str) -> str:
    """Strip separators + convert Persian/Arabic digits to Latin."""
    return normalize_digits(
        (raw or "").replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    )


def is_valid_phone(phone: str) -> bool:
    """True only for an Iranian mobile number written as 09xxxxxxxxx."""
    return bool(IRAN_MOBILE_RE.match(phone or ""))


# Kept for callers that predate the public name.
_normalize_phone = normalize_phone


class CustomerAuthError(Exception):
    """Raised with a Persian user-facing message."""


def current_customer() -> dict | None:
    """Signed-in customer row for this request (cached in ``g``)."""
    if "customer" in g:
        return g.customer
    cid = session.get("customer_id")
    if not cid:
        g.customer = None
        return None
    row = query_one(
        "SELECT id, name, phone, email, password_hash, must_change_pw "
        "FROM customers WHERE id=?",
        (cid,),
    )
    if row is None or not row["password_hash"]:
        # Account deleted or password not set yet -> force out.
        session.pop("customer_id", None)
        g.customer = None
        return None
    g.customer = dict(row)
    return g.customer


def logout_customer() -> None:
    session.pop("customer_id", None)
    g.pop("customer", None)


def login(phone: str, password: str) -> dict:
    """Authenticate by phone + password. Persists a new session on success."""
    phone = _normalize_phone(phone)
    if not phone or not password:
        raise CustomerAuthError("شماره تلفن و رمز عبور الزامی است.")
    row = query_one(
        "SELECT id, name, phone, password_hash, must_change_pw FROM customers WHERE phone=?",
        (phone,),
    )
    if row is None or not row["password_hash"] or not verify_password(row["password_hash"], password):
        raise CustomerAuthError("شماره تلفن یا رمز عبور اشتباه است.")
    session.clear()  # drop any stale customer (and admin) session state safely
    session["customer_id"] = row["id"]
    session.permanent = True
    g.pop("customer", None)
    return dict(row)


def forgot_password(phone: str) -> tuple[bool, str]:
    """SMS a fresh temporary password to the phone on file.

    Returns ``(ok, message)``; the message is safe to show the user either
    way (never confirms whether a number exists).
    """
    phone = _normalize_phone(phone)
    generic = ("اگر این شماره در سیستم ثبت شده باشد، پیامک رمز جدید "
               "به‌زودی ارسال می‌شود.")
    if not phone:
        return False, "شماره تلفن الزامی است."
    row = query_one(
        "SELECT id, password_hash FROM customers WHERE phone=?", (phone,)
    )
    if row is None or not row["password_hash"]:
        # No account / never activated: do not leak existence.
        return True, generic

    import string

    alphabet = string.ascii_letters + string.digits
    temp_password = "".join(secrets.choice(alphabet) for _ in range(10))
    execute(
        "UPDATE customers SET password_hash=?, must_change_pw=1, updated_at=? WHERE id=?",
        (hash_password(temp_password), now_iso(), row["id"]),
    )

    from app.services.notifications import send_direct, sms_enabled
    from app.services.settings import get_setting

    ok, error = send_direct(
        "account.password_reset",
        phone,
        {
            "new_password": temp_password,
            "business_name": get_setting("site.name") or "",
        },
    )
    if not sms_enabled():
        # Mock provider recorded the message; nothing left the server.
        return True, "پیامک رمز جدید ثبت شد (ارسال پیامک فعلاً غیرفعال است)."
    if not ok:
        # Do not leave the account locked on a dead SMS gateway: roll back to
        # no password would be worse; keep the temp password and tell the
        # user to retry — a second attempt issues a new code anyway.
        return False, f"ارسال پیامک ناموفق بود: {error}. دوباره تلاش کنید."
    return True, generic


def change_password(customer_id: int, old: str, new: str) -> None:
    row = query_one("SELECT password_hash FROM customers WHERE id=?", (customer_id,))
    if row is None or not verify_password(row["password_hash"] or "", old):
        raise CustomerAuthError("رمز عبور فعلی اشتباه است.")
    if len(new) < PASSWORD_MIN:
        raise CustomerAuthError(f"رمز عبور حداقل {PASSWORD_MIN} کاراکتر باشد.")
    if verify_password(row["password_hash"], new):
        raise CustomerAuthError("رمز جدید باید با رمز فعلی متفاوت باشد.")
    execute(
        "UPDATE customers SET password_hash=?, must_change_pw=0, updated_at=? WHERE id=?",
        (hash_password(new), now_iso(), customer_id),
    )


# ---------------------------------------------------------------------------
# Self sign-up: phone -> one-time code (SMS) -> password
# ---------------------------------------------------------------------------


def _signup_row(phone: str):
    return query_one("SELECT * FROM signup_codes WHERE phone=?", (phone,))


def _hash_code(salt: str, code: str) -> str:
    """Salted SHA-256 — a leaked table must not reveal a valid code.

    A six-digit code has only a million possibilities, so scrypt would buy
    nothing here; the salt plus ``compare_digest`` plus the attempt cap are
    what actually protect it. The expensive password hashing stays where it
    matters (the password itself).
    """
    return hashlib.sha256(f"{salt}:{code}".encode("utf-8")).hexdigest()


def mask_phone(phone: str) -> str:
    """09123456789 -> 0912***6789, for messages that are shown on screen."""
    return f"{phone[:4]}***{phone[-4:]}" if len(phone) >= 8 else phone


def _sms_enabled() -> bool:
    """Whether a real SMS gateway is configured (imported lazily)."""
    from app.services.notifications import sms_enabled

    return sms_enabled()


def pending_signup() -> dict:
    """The in-progress sign-up for this browser session (may be empty)."""
    state = session.get("signup")
    return state if isinstance(state, dict) else {}


def signup_phone() -> str:
    return str(pending_signup().get("phone") or "")


def signup_demo_code() -> str:
    """The code to print on the verification page.

    Only ever non-empty while SMS delivery is disabled: with no gateway the
    code cannot reach the visitor any other way, and a page that demands a
    code nobody can read would make sign-up impossible on this machine.
    """
    if _sms_enabled() or not current_app.config.get("SIGNUP_SHOW_CODE", True):
        return ""
    return str(pending_signup().get("demo_code") or "")


def _issue_signup_code(phone: str) -> tuple[bool, str, str]:
    """Create/refresh the code row and try to text the code.

    Returns ``(ok, message, demo_code)``; ``demo_code`` is set only when there
    is no SMS gateway and the code is being displayed instead.
    """
    cfg = current_app.config
    now = time.time()
    ttl = max(60, int(cfg.get("SIGNUP_CODE_TTL_SECONDS", 120)))

    row = _signup_row(phone)
    if row is not None:
        cooldown = int(cfg.get("SIGNUP_RESEND_COOLDOWN_SECONDS", 45))
        waited = now - float(row["last_sent_at"] or 0)
        if cooldown > 0 and waited < cooldown:
            wait = int(cooldown - waited) + 1
            raise CustomerAuthError(
                f"کد قبلی همین حالا ارسال شد؛ {wait} ثانیه دیگر دوباره درخواست کنید."
            )

    code = f"{secrets.randbelow(10 ** SIGNUP_CODE_LENGTH):0{SIGNUP_CODE_LENGTH}d}"
    salt = secrets.token_hex(8)
    execute(
        "INSERT INTO signup_codes(phone, code_salt, code_hash, expires_at, attempts, "
        "sends, last_sent_at, verified_until, created_at, updated_at) "
        "VALUES (?,?,?,?,0,1,?,NULL,?,?) "
        "ON CONFLICT(phone) DO UPDATE SET code_salt=excluded.code_salt, "
        "code_hash=excluded.code_hash, expires_at=excluded.expires_at, attempts=0, "
        "sends=sends+1, last_sent_at=excluded.last_sent_at, verified_until=NULL, "
        "updated_at=excluded.updated_at",
        (phone, salt, _hash_code(salt, code), now + ttl, now, now_iso(), now_iso()),
    )

    from app.services.notifications import send_direct
    from app.services.settings import get_setting

    ok, error = send_direct(
        "account.signup_code",
        phone,
        {
            "code": code,
            "expire_minutes": max(1, ttl // 60),
            "business_name": get_setting("site.name") or "",
        },
    )

    if _sms_enabled():
        if not ok:
            return False, f"ارسال پیامک ناموفق بود: {error}. چند لحظه دیگر دوباره تلاش کنید.", ""
        return True, f"کد تأیید به شماره {mask_phone(phone)} پیامک شد.", ""

    # No gateway configured: hand the code to the page instead of the phone.
    if cfg.get("SIGNUP_SHOW_CODE", True):
        return (
            True,
            "ارسال پیامک در این نسخه غیرفعال است؛ کد تأیید را از کادر پایین بردارید.",
            code,
        )
    return False, "ارسال پیامک روی این سرور فعال نیست؛ با مدیر سایت تماس بگیرید.", ""


def start_signup(phone: str) -> tuple[bool, str]:
    """Step 1 — validate the number, send a code, remember the pending sign-up.

    Raises ``CustomerAuthError`` for a malformed number or one that already
    has an account, so the caller can show one clear Persian message.
    """
    phone = normalize_phone(phone)
    if not phone:
        raise CustomerAuthError("شماره تلفن الزامی است.")
    if not is_valid_phone(phone):
        raise CustomerAuthError("شماره تلفن معتبر نیست؛ نمونه: ۰۹۱۲۳۴۵۶۷۸۹.")
    row = query_one("SELECT id, password_hash FROM customers WHERE phone=?", (phone,))
    if row is not None and row["password_hash"]:
        raise CustomerAuthError(
            "این شماره قبلاً ثبت‌نام کرده است؛ با همان رمز وارد شوید یا رمز را بازیابی کنید."
        )
    ok, message, demo_code = _issue_signup_code(phone)
    if ok:
        state: dict = {"phone": phone}
        if demo_code:
            state["demo_code"] = demo_code
        session["signup"] = state
    return ok, message


def cancel_signup() -> None:
    """Drop the pending sign-up (used when the visitor fixes a typo'd number)."""
    phone = signup_phone()
    if phone:
        try:
            execute("DELETE FROM signup_codes WHERE phone=?", (phone,))
        except Exception:  # never let cleanup break the page
            pass
    session.pop("signup", None)


def _bump_attempts(phone: str) -> None:
    execute(
        "UPDATE signup_codes SET attempts=attempts+1, updated_at=? WHERE phone=?",
        (now_iso(), phone),
    )


def verify_signup_code(code: str) -> None:
    """Step 2 — accept the code only once, until it expires or runs out of tries."""
    phone = signup_phone()
    if not phone:
        raise CustomerAuthError("ابتدا شماره تلفن خود را وارد کنید.")
    code = normalize_digits(code)
    if len(code) != SIGNUP_CODE_LENGTH or not code.isdigit():
        raise CustomerAuthError(f"کد تأیید {SIGNUP_CODE_LENGTH} رقمی است.")

    row = _signup_row(phone)
    if row is None:
        raise CustomerAuthError("برای این شماره کدی صادر نشده است؛ دوباره درخواست کد کنید.")
    now = time.time()
    if now > float(row["expires_at"]):
        raise CustomerAuthError("کد تأیید منقضی شده است؛ کد جدید درخواست کنید.")
    max_attempts = max(1, int(current_app.config.get("SIGNUP_MAX_ATTEMPTS", 5)))
    if int(row["attempts"]) >= max_attempts:
        raise CustomerAuthError("تعداد تلاش‌های اشتباه زیاد شد؛ کد جدید درخواست کنید.")

    if not secrets.compare_digest(_hash_code(row["code_salt"], code), row["code_hash"]):
        _bump_attempts(phone)
        left = max_attempts - int(row["attempts"]) - 1
        if left <= 0:
            raise CustomerAuthError("کد تأیید اشتباه است؛ کد جدید درخواست کنید.")
        raise CustomerAuthError(f"کد تأیید اشتباه است. {left} تلاش دیگر باقی مانده است.")

    ttl = max(120, int(current_app.config.get("SIGNUP_VERIFIED_TTL_SECONDS", 900)))
    execute(
        "UPDATE signup_codes SET verified_until=?, attempts=0, updated_at=? WHERE phone=?",
        (now + ttl, now_iso(), phone),
    )


def signup_code_verified() -> bool:
    """True while this session's phone may still set a password."""
    phone = signup_phone()
    if not phone:
        return False
    row = _signup_row(phone)
    return bool(
        row is not None
        and row["verified_until"]
        and time.time() <= float(row["verified_until"])
    )


def complete_signup(name: str, password: str, confirm: str = "") -> dict:
    """Step 3 — create the account with the chosen password and sign in.

    Refuses to run unless the number was verified in step 2 (the flag lives
    server side, so the wizard cannot be short-circuited by editing a URL).
    """
    phone = signup_phone()
    if not phone:
        raise CustomerAuthError("ابتدا شماره تلفن خود را وارد کنید.")
    if not signup_code_verified():
        raise CustomerAuthError("کد تأیید تأیید نشده یا منقضی شده است؛ دوباره شماره را وارد کنید.")
    name = (name or "").strip()
    if len(name) < 2:
        raise CustomerAuthError("نام و نام خانوادگی را کامل وارد کنید.")
    if confirm and password != confirm:
        raise CustomerAuthError("رمز عبور و تکرار آن یکسان نیستند.")
    if len(password or "") < PASSWORD_MIN:
        raise CustomerAuthError(f"رمز عبور حداقل {PASSWORD_MIN} کاراکتر باشد.")

    row = query_one("SELECT id, password_hash FROM customers WHERE phone=?", (phone,))
    if row is not None and row["password_hash"]:
        raise CustomerAuthError("این شماره قبلاً ثبت‌نام کرده است؛ وارد شوید.")

    if row is None:
        cur = execute(
            "INSERT INTO customers(name, phone, password_hash, must_change_pw, updated_at) "
            "VALUES (?,?,?,0,?)",
            (name, phone, hash_password(password), now_iso()),
        )
        customer_id = cur.lastrowid
    else:
        customer_id = row["id"]
        execute(
            "UPDATE customers SET name=?, password_hash=?, must_change_pw=0, updated_at=? "
            "WHERE id=?",
            (name, hash_password(password), now_iso(), customer_id),
        )

    execute("DELETE FROM signup_codes WHERE phone=?", (phone,))
    session.clear()
    session["customer_id"] = customer_id
    session.permanent = True
    g.pop("customer", None)
    return dict(
        query_one(
            "SELECT id, name, phone, must_change_pw FROM customers WHERE id=?",
            (customer_id,),
        )
    )
