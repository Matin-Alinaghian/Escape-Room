"""Permission catalogue and helpers for the two-level administrator system.

The catalogue is data-driven so the super administrator can grant/revoke
individual permissions, and new permissions can be added here without touching
authorisation logic. Enforcement happens in ``app/auth.py`` on **every**
protected endpoint — hiding a button in the frontend is never sufficient.
"""
from __future__ import annotations

# permission key -> (Persian label, group)
PERMISSIONS: dict[str, tuple[str, str]] = {
    "dashboard.view": ("مشاهده داشبورد", "داشبورد"),
    "scenarios.manage": ("مدیریت سناریوها", "سناریوها"),
    "media.manage": ("بارگذاری و مدیریت تصاویر", "رسانه"),
    "schedules.manage": ("مدیریت برنامه و زمان‌بندی", "زمان‌بندی"),
    "bookings.view": ("مشاهده رزروها", "رزروها"),
    "bookings.create": ("ایجاد رزرو", "رزروها"),
    "bookings.edit": ("ویرایش رزرو", "رزروها"),
    "bookings.cancel": ("لغو رزرو", "رزروها"),
    "bookings.complete": ("تکمیل رزرو", "رزروها"),
    "customers.view": ("مشاهده اطلاعات مشتریان", "مشتریان"),
    "reviews.view": ("مشاهده نظرات", "نظرات"),
    "reviews.moderate": ("مدیریت و تأیید نظرات", "نظرات"),
    "content.edit": ("ویرایش محتوای سایت", "محتوا"),
    "reports.view": ("مشاهده گزارش‌ها", "گزارش‌ها"),
    "export": ("خروجی گرفتن از داده‌ها", "گزارش‌ها"),
    "notifications.manage": ("مدیریت قالب پیامک و اعلان‌ها", "اعلان‌ها"),
    "sms.settings": ("دسترسی به تنظیمات پیامک", "اعلان‌ها"),
    "payment.settings": ("دسترسی به تنظیمات پرداخت", "پرداخت"),
    "logs.view": ("مشاهده گزارش فعالیت مدیران", "امنیت"),
}

# Permissions only the super administrator may ever hold.
SUPER_ONLY = {"admins.manage", "logs.view"}

ALL_PERMISSIONS = sorted(PERMISSIONS.keys())


def permission_label(key: str) -> str:
    return PERMISSIONS.get(key, (key, ""))[0]


def permission_group(key: str) -> str:
    return PERMISSIONS.get(key, (key, "سایر"))[1]


def grouped_permissions() -> dict[str, list[tuple[str, str]]]:
    """Group catalogue for the permission editor UI."""
    groups: dict[str, list[tuple[str, str]]] = {}
    for key, (label, group) in PERMISSIONS.items():
        groups.setdefault(group, []).append((key, label))
    return groups
