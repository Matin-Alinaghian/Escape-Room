"""Admin → operational settings: booking rules, notification templates, integrations."""
from __future__ import annotations

from flask import current_app, flash, redirect, render_template, request, url_for

from app.auth import current_admin, require_permission
from app.blueprints.admin import bp
from app.db import execute, now_iso, query, query_one
from app.services.audit import log_action
from app.services.notifications import (
    DEFAULT_TEMPLATES,
    ALLOWED_PLACEHOLDERS,
    get_template,
    sms_enabled,
)
from app.services.settings import get_setting, set_many

BOOKING_NUMERIC = {
    "booking.min_notice_hours": (0, 168),
    "booking.cancel_window_hours": (0, 168),
    "booking.default_price": (0, 100_000_000),
}

EVENT_LABELS = {
    "booking.new": "رزرو جدید",
    "booking.confirmed": "تأیید رزرو",
    "booking.cancelled": "لغو رزرو",
    "booking.completed": "تکمیل بازی",
    "booking.reminder": "یادآوری",
    "review.invite": "دعوت به ثبت نظر",
    "account.signup_code": "کد ثبت‌نام",
    "account.password_reset": "بازیابی رمز عبور",
}


def _to_en_digits(raw: str) -> str:
    return (raw or "").translate(
        str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩٫", "01234567890123456789.")
    )


@bp.route("/settings")
@require_permission("content.edit")
def settings_index():
    return render_template(
        "admin/settings/index.html",
        booking={
            "enabled": bool(get_setting("booking.enabled", True)),
            "min_notice_hours": get_setting("booking.min_notice_hours", 2),
            "cancel_window_hours": get_setting("booking.cancel_window_hours", 12),
            "currency": get_setting("booking.currency", "تومان"),
            "default_price": get_setting("booking.default_price", 350000),
        },
        review={
            "invite_delay_hours": get_setting("review.invite_delay_hours", 24),
            "auto_approve": bool(get_setting("review.auto_approve", False)),
        },
        templates=[
            {
                "event": ev,
                "label": EVENT_LABELS.get(ev, ev),
                "body": get_template(ev) or DEFAULT_TEMPLATES.get(ev, ""),
                "active": bool(
                    (query_one(
                        "SELECT is_active FROM notification_templates WHERE event_type=?",
                        (ev,),
                    ) or {"is_active": 1})["is_active"]
                ),
            }
            for ev in DEFAULT_TEMPLATES
        ],
        placeholders=sorted(ALLOWED_PLACEHOLDERS),
        integrations={
            "sms": {
                "enabled": bool(current_app.config.get("SMS_ENABLED")),
                "provider": current_app.config.get("SMS_PROVIDER", "mock"),
                "has_key": bool(current_app.config.get("SMS_API_KEY")),
            },
            "payment": {
                "enabled": bool(current_app.config.get("PAYMENT_ENABLED")),
                "provider": current_app.config.get("PAYMENT_PROVIDER", "mock"),
                "has_key": bool(current_app.config.get("PAYMENT_API_KEY")),
            },
        },
        sound_enabled=bool(get_setting("sound.enabled", True)),
        sound_volume=get_setting("sound.volume", 0.18),
        site_url=current_app.config.get("SITE_URL", ""),
        timezone=current_app.config.get("TIMEZONE", ""),
    )


@bp.route("/settings", methods=["POST"])
@require_permission("content.edit")
def settings_save():
    action = request.form.get("action", "booking")

    if action == "booking":
        payload: dict = {"booking.enabled": bool(request.form.get("booking.enabled"))}
        for key, (low, high) in BOOKING_NUMERIC.items():
            raw = _to_en_digits((request.form.get(key) or "").strip())
            try:
                value = int(raw)
            except ValueError:
                flash(f"مقدار «{key}» باید عدد باشد.", "error")
                return redirect(url_for("admin.settings_index"))
            if not low <= value <= high:
                flash(f"مقدار «{key}» باید بین {low} و {high} باشد.", "error")
                return redirect(url_for("admin.settings_index"))
            payload[key] = value
        payload["booking.currency"] = (request.form.get("booking.currency") or "تومان").strip()[:20]
        set_many(payload)
        log_action("settings.booking", ", ".join(payload), entity_type="settings")
        flash("تنظیمات رزرو ذخیره شد.", "success")

    elif action == "reviews":
        delay = _to_en_digits((request.form.get("review.invite_delay_hours") or "24").strip())
        try:
            delay = int(delay)
        except ValueError:
            delay = 24
        set_many(
            {
                "review.invite_delay_hours": max(min(delay, 720), 0),
                "review.auto_approve": bool(request.form.get("review.auto_approve")),
            }
        )
        flash("تنظیمات نظرات ذخیره شد.", "success")

    elif action == "templates":
        for event in request.form.getlist("event"):
            body = request.form.get(f"body_{event}") or ""
            active = 1 if request.form.get(f"active_{event}") else 0
            execute(
                "INSERT INTO notification_templates(event_type, body, is_active, updated_at) "
                "VALUES (?,?,?,?) ON CONFLICT(event_type) DO UPDATE SET "
                "body=excluded.body, is_active=excluded.is_active, updated_at=excluded.updated_at",
                (event, body.strip(), active, now_iso()),
            )
        log_action("settings.templates", "قالب پیام‌ها", entity_type="notification_templates")
        flash("قالب پیام‌ها ذخیره شد.", "success")

    elif action == "sound":
        raw_volume = _to_en_digits((request.form.get("sound.volume") or "").strip())
        try:
            volume = float(raw_volume)
        except ValueError:
            volume = -1.0  # keep the previously stored value
        if 0 <= volume <= 1:
            set_many({
                "sound.enabled": bool(request.form.get("sound.enabled")),
                "sound.volume": volume,
            })
            flash("تنظیمات صدا ذخیره شد.", "success")
        elif volume < 0:
            set_many({"sound.enabled": bool(request.form.get("sound.enabled"))})
            flash("تنظیمات صدا ذخیره شد.", "success")
        else:
            set_many({"sound.enabled": bool(request.form.get("sound.enabled"))})
            flash("میزان صدا باید بین ۰ و ۱ باشد؛ سایر تنظیمات ذخیره شد.", "error")

    else:
        flash("عملیات نامعتبر است.", "error")

    return redirect(url_for("admin.settings_index"))


@bp.route("/settings/notifications/run", methods=["POST"])
@require_permission("notifications.manage")
def settings_run_notifications():
    from app.services.notifications import process_due_notifications

    sent = process_due_notifications()
    log_action("notifications.run", f"{sent} ارسال", entity_type="notifications")
    if not sms_enabled():
        flash("ارسال پیامک غیرفعال است — فقط صف ثبت شد.", "info")
    else:
        flash(f"{sent} پیام ارسال شد.", "success")
    return redirect(url_for("admin.settings_index"))


@bp.route("/settings/notifications", methods=["GET"])
@require_permission("notifications.manage")
def settings_notifications():
    rows = query("SELECT * FROM notifications ORDER BY created_at DESC LIMIT 300")
    stats = {
        r["status"]: r["c"]
        for r in query("SELECT status, COUNT(*) AS c FROM notifications GROUP BY status")
    }
    return render_template(
        "admin/settings/notifications.html", rows=rows, counts=stats, sms_enabled=sms_enabled()
    )