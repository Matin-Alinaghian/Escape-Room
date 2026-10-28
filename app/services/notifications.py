"""Notification service — fully functional with zero external services.

Design
------
* ``SmsProvider`` is an interface with a **mock** implementation (default) and
  a generic HTTP implementation ready for a real Iranian SMS gateway.
* Nothing is ever sent while ``SMS_ENABLED=0``: records are written with
  ``status='skipped'`` so the admin can still see what *would* have been sent.
* ``dedupe_key`` is UNIQUE, so retried jobs can never double-send a message.
* Persian templates use ``{placeholders}``; unknown placeholders are left
  untouched and all values are HTML-escaped for safety.
"""
from __future__ import annotations

import html
import json
import re
import secrets
from datetime import datetime, timedelta, timezone

from app.db import execute, now_iso, query, query_one

# --------------------------------------------------------------------------
# Templates
# --------------------------------------------------------------------------

DEFAULT_TEMPLATES: dict[str, str] = {
    "booking.new": (
        "رزرو جدید ثبت شد.\n"
        "کد رهگیری: {booking_code}\nاتاق: {scenario}\nتاریخ: {date} ساعت {time}\n"
        "نفرات: {participants}\nنام: {customer_name}"
    ),
    "booking.confirmed": (
        "رزرو شما تأیید شد. ✅\nکد رهگیری: {booking_code}\nاتاق: {scenario}\n"
        "تاریخ: {date} ساعت {time}\nنفرات: {participants}\nمنتظر دیدار شما هستیم."
    ),
    "booking.cancelled": (
        "رزرو شما لغو شد. ❌\nکد رهگیری: {booking_code}\nاتاق: {scenario}\nتاریخ: {date} ساعت {time}"
    ),
    "booking.completed": (
        "بازی شما به پایان رسید. 🎉\nکد رهگیری: {booking_code}\nاتاق: {scenario}\n"
        "امیدواریم تجربه خوبی داشته باشید."
    ),
    "booking.reminder": (
        "یادآوری: رزرو شما فردا برگزار می‌شود. ⏰\nکد رهگیری: {booking_code}\n"
        "اتاق: {scenario}\nتاریخ: {date} ساعت {time}"
    ),
    "review.invite": (
        "از بازی در «{scenario}» لذت بردید؟ نظرتان را با ما در میان بگذارید.\n"
        "لینک ثبت نظر: {review_link}"
    ),
    "account.password_reset": (
        "رمز عبور جدید شما: {new_password}\n"
        "پس از ورود می‌توانید آن را تغییر دهید.\n{business_name}"
    ),
    "account.signup_code": (
        "کد تأیید ثبت‌نام شما: {code}\n"
        "این کد تا {expire_minutes} دقیقه اعتبار دارد.\n"
        "اگر شما درخواست نداده‌اید، این پیام را نادیده بگیرید.\n{business_name}"
    ),
}

PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")
ALLOWED_PLACEHOLDERS = {
    "booking_code", "scenario", "date", "time", "participants",
    "customer_name", "review_link", "price", "status", "business_name",
    "new_password", "code", "expire_minutes",
}


def ensure_templates() -> None:
    """Insert default Persian templates (idempotent)."""
    for event, body in DEFAULT_TEMPLATES.items():
        execute(
            "INSERT OR IGNORE INTO notification_templates(event_type, body, is_active) VALUES (?,?,1)",
            (event, body),
        )


def get_template(event: str) -> str:
    ensure_templates()
    row = query_one("SELECT body, is_active FROM notification_templates WHERE event_type=?", (event,))
    if row is None or not row["is_active"]:
        return ""
    return row["body"]


def render_template(body: str, context: dict) -> str:
    """Replace known placeholders; escape values; never raise."""

    def sub(match: re.Match) -> str:
        key = match.group(1)
        if key not in ALLOWED_PLACEHOLDERS:
            return match.group(0)
        value = context.get(key, "")
        if value is None:
            value = ""
        return html.escape(str(value))

    return PLACEHOLDER_RE.sub(sub, body)


# --------------------------------------------------------------------------
# Providers
# --------------------------------------------------------------------------


class SmsProvider:
    """Interface every concrete gateway must implement."""

    name = "base"

    def send(self, to: str, body: str) -> tuple[bool, str]:
        raise NotImplementedError


class MockSmsProvider(SmsProvider):
    """Development provider: records the message, contacts nobody."""

    name = "mock"

    def send(self, to: str, body: str) -> tuple[bool, str]:
        # Deliberately no network I/O. Real delivery requires a paid provider.
        return True, "mock: no message left the server"


class HttpSmsProvider(SmsProvider):
    """Generic JSON gateway — configured through the SMS settings screen.

    Adapts to the common Iranian SMS-panel pattern (POST with API key +
    sender number). The exact field names are configurable, so no code change
    is needed when the owner picks a provider.
    """

    name = "http"

    def __init__(self, base_url: str, api_key: str, api_secret: str, sender: str,
                 field_to: str = "to", field_text: str = "text"):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.api_secret = api_secret
        self.sender = sender
        self.field_to = field_to
        self.field_text = field_text

    def send(self, to: str, body: str) -> tuple[bool, str]:
        import requests

        try:
            resp = requests.post(
                self.base_url,
                json={
                    self.field_to: to,
                    self.field_text: body,
                    "from": self.sender,
                    "apiKey": self.api_key,
                    "apiSecret": self.api_secret,
                },
                timeout=12,
            )
        except Exception as exc:
            return False, f"network: {exc}"
        if resp.status_code >= 400:
            return False, f"http {resp.status_code}: {resp.text[:200]}"
        return True, "sent"


def get_provider():
    from flask import current_app

    cfg = current_app.config
    if cfg.get("SMS_ENABLED") and cfg.get("SMS_PROVIDER") == "http" and cfg.get("SMS_BASE_URL"):
        return HttpSmsProvider(
            cfg["SMS_BASE_URL"], cfg.get("SMS_API_KEY", ""),
            cfg.get("SMS_API_SECRET", ""), cfg.get("SMS_SENDER", ""),
        )
    return MockSmsProvider()


def sms_enabled() -> bool:
    from flask import current_app

    return bool(current_app.config.get("SMS_ENABLED"))


# --------------------------------------------------------------------------
# Booking context
# --------------------------------------------------------------------------


def _context(reservation_id: int) -> dict:
    from app.jalali import format_jalali

    row = query_one(
        "SELECT r.*, s.title AS scenario_title, c.name AS customer_name, c.phone "
        "FROM reservations r JOIN scenarios s ON s.id=r.scenario_id "
        "JOIN customers c ON c.id=r.customer_id WHERE r.id=?",
        (reservation_id,),
    )
    if row is None:
        return {}
    try:
        date_text = format_jalali(datetime.strptime(row["slot_date"], "%Y-%m-%d").date())
    except ValueError:
        date_text = row["slot_date"]
    from app.services.settings import get_setting

    return {
        "booking_code": row["code"],
        "scenario": row["scenario_title"],
        "date": date_text,
        "time": row["start_time"],
        "participants": row["participants"],
        "customer_name": row["customer_name"],
        "price": f"{row['total_price']:,}",
        "status": row["status"],
        "business_name": get_setting("site.name"),
        "_phone": row["phone"],
        "_reservation_id": row["id"],
    }


# --------------------------------------------------------------------------
# Queue / delivery
# --------------------------------------------------------------------------

MAX_ATTEMPTS = 3


def _insert(event: str, context: dict, dedupe_key: str, reservation_id=None,
            recipient: str = "", status: str = "queued", payload: dict | None = None) -> int | None:
    body = render_template(get_template(event), context)
    if not body:
        return None
    existing = query_one("SELECT id FROM notifications WHERE dedupe_key=?", (dedupe_key,))
    if existing:
        return None  # duplicate prevention
    cur = execute(
        "INSERT INTO notifications(event_type, reservation_id, booking_code, recipient, provider, "
        "status, payload, dedupe_key, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            event, reservation_id, context.get("booking_code"), recipient,
            "mock" if not sms_enabled() else get_provider().name,
            status, json.dumps({"text": body, **(payload or {})}, ensure_ascii=False),
            dedupe_key, now_iso(),
        ),
    )
    return cur.lastrowid


def enqueue_event(event: str, reservation_id: int) -> None:
    """Record a notification for a booking event (and deliver if enabled)."""
    try:
        context = _context(reservation_id)
        if not context:
            return
        phone = context.pop("_phone", "")
        code = context["booking_code"]
        enabled = sms_enabled()
        status = "queued" if enabled else "skipped"
        row_id = _insert(
            event, context, dedupe_key=f"{event}:{code}",
            reservation_id=reservation_id, recipient=phone, status=status,
            payload={"not_before": None},
        )
        if row_id and enabled:
            _deliver(row_id)
    except Exception:  # notifications must never break a booking
        pass


def schedule_review_invite(reservation_id: int) -> None:
    """Queue the post-visit review invitation (sent once, after the delay)."""
    try:
        from app.services.settings import get_setting

        context = _context(reservation_id)
        if not context:
            return
        phone = context.pop("_phone", "")
        code = context["booking_code"]
        from flask import current_app

        base = current_app.config.get("SITE_URL", "")
        context["review_link"] = f"{base}/review/{code}"
        delay_h = int(get_setting("review.invite_delay_hours", 24) or 24)
        not_before = (datetime.now(timezone.utc) + timedelta(hours=delay_h)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        _insert(
            "review.invite", context, dedupe_key=f"review.invite:{code}",
            reservation_id=reservation_id, recipient=phone,
            status="queued", payload={"not_before": not_before},
        )
    except Exception:
        pass


def _deliver(notification_id: int) -> bool:
    """Attempt delivery once; record attempts, status and last error."""
    row = query_one("SELECT * FROM notifications WHERE id=?", (notification_id,))
    if row is None or row["status"] in ("sent", "skipped"):
        return False
    if row["attempts"] >= MAX_ATTEMPTS:
        execute(
            "UPDATE notifications SET status='failed', last_error=? WHERE id=?",
            ("max attempts reached", notification_id),
        )
        return False

    try:
        payload = json.loads(row["payload"] or "{}")
    except ValueError:
        payload = {}
    body = payload.get("text", "")
    provider = get_provider()
    ok, error = provider.send(row["recipient"], body)

    attempts = row["attempts"] + 1
    if ok:
        execute(
            "UPDATE notifications SET status='sent', attempts=?, provider=?, last_error=NULL, sent_at=? WHERE id=?",
            (attempts, provider.name, now_iso(), notification_id),
        )
        return True

    status = "failed" if attempts >= MAX_ATTEMPTS else "queued"
    execute(
        "UPDATE notifications SET status=?, attempts=?, provider=?, last_error=? WHERE id=?",
        (status, attempts, provider.name, error[:500], notification_id),
    )
    return False


def process_due_notifications(limit: int = 20) -> int:
    """Deliver queued messages whose time has come (invites + retries).

    Called opportunistically from the app; safe to call repeatedly — the
    unique ``dedupe_key`` guarantees a message is never sent twice.
    """
    if not sms_enabled():
        return 0
    now = now_iso()
    rows = query(
        "SELECT id, payload FROM notifications WHERE status='queued' ORDER BY created_at LIMIT ?",
        (limit,),
    )
    sent = 0
    for row in rows:
        try:
            payload = json.loads(row["payload"] or "{}")
        except ValueError:
            payload = {}
        not_before = payload.get("not_before")
        if not_before and not_before > now:
            continue
        if _deliver(row["id"]):
            sent += 1
    return sent


def send_direct(event: str, phone: str, context: dict) -> tuple[bool, str]:
    """Send a non-booking SMS immediately (e.g. account password reset).

    Unlike booking notifications there is no dedupe key here — each reset
    must always deliver. The rendered body is still archived in the
    notifications table for the admin's audit trail, marked ``sent``/
    ``failed`` accordingly. Never raises.
    """
    try:
        body = render_template(get_template(event), context)
        if not body:
            return False, "template disabled"
        provider = get_provider()
        ok, error = provider.send(phone, body)
        try:
            execute(
                "INSERT INTO notifications(event_type, recipient, provider, status, attempts, "
                "last_error, payload, dedupe_key, created_at, sent_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    event, phone, provider.name,
                    "sent" if ok else "failed", 1,
                    None if ok else (error or "")[:500],
                    json.dumps({"text": body, **context}, ensure_ascii=False, default=str),
                    # Direct sends are never deduped — the key only has to be
                    # unique per row. now_iso() resolves to one second, so two
                    # codes for the same number inside the same second (an
                    # OTP resend always is) would collide and the archive row
                    # would be silently dropped.
                    f"{event}:{phone}:{now_iso()}:{secrets.token_hex(4)}",
                    now_iso(), now_iso() if ok else None,
                ),
            )
        except Exception:
            # The message itself already went out; a storage hiccup must not
            # be reported to the user as a delivery failure.
            pass
        return ok, error
    except Exception as exc:  # never break the auth flow
        return False, f"error: {exc}"
