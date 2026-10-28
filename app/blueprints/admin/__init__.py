"""Administrator blueprint package.

The blueprint is created here and the view modules are imported at the bottom
so every route lands on the same ``admin`` blueprint with the ``/admin``
prefix enforced by the application factory.

Imports are wrapped so one broken view module degrades to a warning instead of
taking the whole application down at startup.
"""
from __future__ import annotations

import warnings

from flask import Blueprint, request, url_for

bp = Blueprint("admin", __name__)

_IMPORT_ERRORS = []


def _import(module_name: str):
    import importlib

    try:
        importlib.import_module(f"app.blueprints.admin.{module_name}")
    except Exception as exc:  # pragma: no cover - defensive
        _IMPORT_ERRORS.append((module_name, exc))


for _mod in (
    "views_auth",
    "views_dashboard",
    "views_rooms",
    "views_bookings",
    "views_schedules",
    "views_users",
    "views_content",
    "views_media",
    "views_reviews",
    "views_reports",
    "views_settings",
    "views_logs",
):
    _import(_mod)

# Fire a warning once at startup if anything failed to import so the
# operator can chase it down without the app crashing.
if _IMPORT_ERRORS:  # pragma: no cover - startup diagnostics
    warnings.warn(
        "Some admin view modules failed to import: "
        + "; ".join(f"{m}: {e}" for m, e in _IMPORT_ERRORS),
        stacklevel=2,
    )


# --------------------------------------------------------------------------
# Sidebar
# --------------------------------------------------------------------------

# (endpoint, label, icon, required permission or None for always-visible)
NAV_ITEMS = (
    ("admin.dashboard", "داشبورد", "grid", "dashboard.view"),
    ("admin.rooms_index", "اتاق‌ها", "door", "scenarios.manage"),
    ("admin.schedules_index", "زمان‌بندی", "calendar", "schedules.manage"),
    ("admin.bookings_waitlist", "لیست انتظار", "list", "bookings.view"),
    ("admin.bookings_index", "رزروها", "ticket", "bookings.view"),
    ("admin.customers_index", "مشتریان", "users", "customers.view"),
    ("admin.reviews_index", "نظرات", "star", "reviews.view"),
    ("admin.content_index", "محتوا", "page", "content.edit"),
    ("admin.media_index", "رسانه", "image", "media.manage"),
    ("admin.reports_index", "گزارش‌ها", "chart", "reports.view"),
    ("admin.settings_index", "تنظیمات", "cog", "content.edit"),
    ("admin.logs_index", "گزارش فعالیت", "list", "logs.view"),
)

NAV_ICONS = {
    "grid": "M4 4h6v6H4V4zm10 0h6v6h-6V4zM4 14h6v6H4v-6zm10 0h6v6h-6v-6z",
    "door": "M6 3h12v18H6V3zm2 2v14h8V5H8zm4 5a1.5 1.5 0 110 3 1.5 1.5 0 010-3z",
    "calendar": "M7 3v3M17 3v3M4 8h16M5 5h14a1 1 0 011 1v13a1 1 0 01-1 1H5a1 1 0 01-1-1V6a1 1 0 011-1zm3 7h3v3H8v-3z",
    "ticket": "M4 8a2 2 0 002-2h12a2 2 0 012 2v1a2 2 0 000 4v1a2 2 0 01-2 2H6a2 2 0 01-2-2v-1a2 2 0 000-4V8zm9-1h2v10h-2V7z",
    "users": "M9 11a3.5 3.5 0 100-7 3.5 3.5 0 000 7zm7 0a3 3 0 100-6 3 3 0 000 6zM3 20c0-3.3 2.7-5 6-5s6 1.7 6 5v1H3v-1zm14 1v-1c0-1.9-.8-3.4-2.2-4.3 3.2.2 6.2 1.7 6.2 5.3v1h-4z",
    "star": "M12 3l2.6 5.8 6.4.7-4.8 4.3 1.3 6.2L12 17l-5.5 3 1.3-6.2L3 9.5l6.4-.7L12 3z",
    "page": "M6 3h8l4 4v14H6V3zm7 1.5V8h3.5L13 4.5zM8 12h8v2H8v-2zm0 4h8v2H8v-2z",
    "image": "M4 5h16v14H4V5zm2 2v10h12V7H6zm2 8l2.5-3 2 2.4 2.5-3.5L18 14H8zm1.5-5a1.2 1.2 0 110 2.4A1.2 1.2 0 019.5 10z",
    "chart": "M4 20V9h4v11H4zm6 0V4h4v16h-4zm6 0v-7h4v7h-4z",
    "cog": "M12 8.5A3.5 3.5 0 1015.5 12 3.5 3.5 0 0012 8.5zm8.5 3.5l2 1.2-1.6 2.8-2.2-.8a7.6 7.6 0 01-1.8 1l-.4 2.3h-3.2l-.4-2.3a7.6 7.6 0 01-1.8-1l-2.2.8L7.5 13.2l2-1.2a7.6 7.6 0 010-2.4l-2-1.2 1.6-2.8 2.2.8a7.6 7.6 0 011.8-1L13.5 3h3.2l.4 2.3c.6.3 1.2.6 1.8 1l2.2-.8 1.6 2.8-2 1.2a7.6 7.6 0 010 2.4z",
    "list": "M4 6h16v2H4V6zm0 5h16v2H4v-2zm0 5h16v2H4v-2z",
}


@bp.context_processor
def inject_admin_nav():
    """Sidebar entries the signed-in administrator is actually allowed to see."""
    from app.auth import admin_permissions, current_admin

    me = current_admin()
    if me is None:
        return {"admin_nav": [], "current_path": request.path}

    allowed = admin_permissions(me)
    items = []
    for endpoint, label, icon, permission in NAV_ITEMS:
        if permission and me["role"] != "super" and permission not in allowed:
            continue
        items.append(
            {
                "endpoint": endpoint,
                "label": label,
                "icon": icon,
                "path": _path_for(endpoint),
            }
        )
    from app.auth import has_permission

    return {
        "admin_nav": items,
        "nav_icons": NAV_ICONS,
        "current_path": request.path,
        "me": me,
        # Lets a template hide an action the signed-in admin cannot perform
        # instead of offering a button that would only flash an error.
        "can": has_permission,
    }


def _path_for(endpoint: str) -> str:
    try:
        return url_for(endpoint)
    except Exception:  # pragma: no cover - blueprint not registered yet
        return "/admin/"