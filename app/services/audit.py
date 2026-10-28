"""Append-only audit logging for administrative actions."""
from __future__ import annotations

from flask import has_request_context, request

from app.db import execute, now_iso
from app.security import client_ip


def log_action(
    action: str,
    detail: str = "",
    *,
    entity_type: str | None = None,
    entity_id: str | int | None = None,
    admin: dict | None = None,
    admin_id: int | None = None,
    admin_name: str | None = None,
) -> None:
    """Record an audit entry. Never raises — auditing must not break a request."""
    try:
        if admin is None and admin_id is None and has_request_context():
            # Outside a request (CLI commands) there is no session to resolve
            # an actor from — calling current_admin() would raise and the
            # whole entry would be silently dropped.
            from app.auth import current_admin

            admin = current_admin()
        if admin is not None:
            admin_id = admin.get("id")
            admin_name = admin.get("username") or admin.get("display_name")

        ip = client_ip() if has_request_context() else None
        execute(
            "INSERT INTO audit_logs(admin_id, admin_name, action, entity_type, "
            "entity_id, detail, ip, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                admin_id,
                admin_name,
                action,
                entity_type,
                str(entity_id) if entity_id is not None else None,
                detail[:2000],
                ip,
                now_iso(),
            ),
        )
    except Exception:  # pragma: no cover - defensive
        try:
            if has_request_context():
                request.environ.get("werkzeug.server.shutdown")
        except Exception:
            pass
