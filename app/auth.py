"""Authentication, sessions and permission enforcement.

Two-level administrator model
-----------------------------
``super``   — exactly one account (enforced by a partial unique index), holds
              every capability implicitly, and is the only role that can reach
              the administrator-management surface.
``secondary``— holds only the rows present in ``admin_permissions``.

Enforcement happens in :func:`require_permission` on **every** protected
endpoint. Permission sets are re-read from the database on each request so a
revocation takes effect immediately (no stale session cache).
"""
from __future__ import annotations

import functools
from typing import Callable

from flask import g, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from app.db import query_one, now_iso, execute
from app.permissions import SUPER_ONLY

# --------------------------------------------------------------------------
# Passwords
# --------------------------------------------------------------------------


def hash_password(password: str) -> str:
    return generate_password_hash(password, method="scrypt:32768:16:1")


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return check_password_hash(password_hash, password)
    except Exception:
        return False


# --------------------------------------------------------------------------
# Current identity
# --------------------------------------------------------------------------


def current_admin() -> dict | None:
    """Return the signed-in administrator row (cached for this request)."""
    if "admin" in g:
        return g.admin
    admin_id = session.get("admin_id")
    if not admin_id:
        g.admin = None
        return None
    row = query_one(
        "SELECT id, username, display_name, role, is_active, must_change_pw "
        "FROM admins WHERE id = ?",
        (admin_id,),
    )
    if row is None or not row["is_active"]:
        # Account deleted or deactivated while signed in -> force out.
        session.clear()
        g.admin = None
        return None
    g.admin = dict(row)
    return g.admin


def is_super() -> bool:
    admin = current_admin()
    return bool(admin and admin["role"] == "super")


def permissions_for(admin_id: int) -> set[str]:
    from app.db import query

    return {
        r["permission"]
        for r in query("SELECT permission FROM admin_permissions WHERE admin_id=?", (admin_id,))
    }


def admin_permissions(admin: dict | None = None) -> set[str]:
    """Effective permission set for the signed-in administrator."""
    admin = admin or current_admin()
    if not admin:
        return set()
    if admin["role"] == "super":
        # Super administrator implicitly holds everything, including
        # capabilities that are not part of the public catalogue.
        from app.permissions import ALL_PERMISSIONS

        return set(ALL_PERMISSIONS) | set(SUPER_ONLY)
    return permissions_for(admin["id"])


def has_permission(key: str) -> bool:
    admin = current_admin()
    if not admin:
        return False
    if admin["role"] == "super":
        return True
    return key in admin_permissions(admin)


# --------------------------------------------------------------------------
# Decorators
# --------------------------------------------------------------------------


def login_required(view: Callable):
    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        if current_admin() is None:
            if request.path.startswith("/api/") or request.accept_mimetypes.best == "application/json":
                return {"error": "authentication_required"}, 401
            return redirect(url_for("admin.login", next=request.full_path))
        return view(*args, **kwargs)

    return wrapper


def super_admin_required(view: Callable):
    """Absolute guard for administrator management and audit-log routes."""

    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        admin = current_admin()
        if admin is None:
            return redirect(url_for("admin.login", next=request.full_path))
        if admin["role"] != "super":
            return {"error": "forbidden", "detail": "super_admin_only"}, 403
        return view(*args, **kwargs)

    return wrapper


def require_permission(key: str):
    """Backend authorisation for a single permission key."""

    def decorator(view: Callable):
        @functools.wraps(view)
        def wrapper(*args, **kwargs):
            admin = current_admin()
            if admin is None:
                if request.path.startswith("/api/"):
                    return {"error": "authentication_required"}, 401
                return redirect(url_for("admin.login", next=request.full_path))
            if admin["role"] != "super" and key not in admin_permissions(admin):
                from app.services.audit import log_action

                log_action(
                    action="permission.denied",
                    detail=f"{key} on {request.method} {request.path}",
                    admin=admin,
                )
                if request.path.startswith("/api/"):
                    return {"error": "forbidden", "permission": key}, 403
                return {"error": "forbidden", "permission": key}, 403
            return view(*args, **kwargs)

        return wrapper

    return decorator


# --------------------------------------------------------------------------
# Session lifecycle
# --------------------------------------------------------------------------


def login_admin(admin_id: int, ip: str | None = None) -> None:
    session.clear()
    session["admin_id"] = admin_id
    session.permanent = True
    g.pop("admin", None)
    execute("UPDATE admins SET last_login_at=?, last_login_ip=? WHERE id=?", (now_iso(), ip, admin_id))


def logout_admin(admin_id: int | None) -> None:
    session.clear()
    if admin_id:
        from app.services.audit import log_action

        log_action(action="admin.logout", detail="signed out", admin_id=admin_id)


def change_password(admin_id: int, new_password: str) -> None:
    execute(
        "UPDATE admins SET password_hash=?, must_change_pw=0, updated_at=? WHERE id=?",
        (hash_password(new_password), now_iso(), admin_id),
    )
