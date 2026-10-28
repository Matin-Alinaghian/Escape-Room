"""Security middleware: CSRF protection, rate limiting, security headers."""
from __future__ import annotations

import secrets
import threading
import time

from flask import Flask, jsonify, render_template, request, session

CSRF_FIELD = "_csrf_token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
# Endpoints reached by a third-party server (payment gateways redirecting the
# browser) — they carry no user session token and their payload is re-verified
# server side against the gateway, so CSRF does not apply.
CSRF_EXEMPT_PREFIXES = ("/payment/callback",)


def generate_csrf_token() -> str:
    if "_csrf_token" not in session:
        session["_csrf_token"] = secrets.token_urlsafe(32)
    return session["_csrf_token"]


def _csrf_failure_response():
    """Reject an unsafe request without dumping raw JSON on a browser form.

    Machine clients (``/api/...``, XHR sending ``X-CSRF-Token`` or JSON) still
    get a JSON 403. A normal HTML form post gets a friendly, styled 403 page
    (never raw JSON) with a link back to where they came from — the common
    case is a stale or cached form, e.g. a page left open after the session
    cookie changed. The status stays 403 so the security contract holds.
    """
    wants_json = (
        request.path.startswith("/api/")
        or request.is_json
        or bool(request.headers.get("X-CSRF-Token"))
        or "application/json" in (request.headers.get("Accept") or "")
    )
    if wants_json:
        return jsonify({"error": "csrf_failed"}), 403

    referrer = request.referrer or ""
    back_url = ""
    if referrer.startswith(request.host_url):
        back_url = referrer
    if not back_url:
        # No usable referrer (privacy setting, bookmark): go back to the form
        # itself when that route also serves GET, otherwise to the home page.
        rule = request.url_rule
        if rule is not None and "GET" in (rule.methods or set()):
            back_url = request.path
        else:
            back_url = "/"
    return render_template("errors/csrf.html", back_url=back_url), 403


def validate_csrf() -> None:
    """Reject unsafe requests whose CSRF token is missing or mismatched."""
    if request.method in SAFE_METHODS:
        return
    sent = request.form.get(CSRF_FIELD) or request.headers.get("X-CSRF-Token")
    expected = session.get("_csrf_token")
    if not expected or not sent or not secrets.compare_digest(sent, expected):
        return _csrf_failure_response()


# --------------------------------------------------------------------------
# Sliding-window rate limiter (process local)
# --------------------------------------------------------------------------


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, bucket: str, key: str, limit: int, window: float = 60.0) -> bool:
        if limit <= 0:
            return True
        token = f"{bucket}:{key}"
        now = time.monotonic()
        with self._lock:
            hits = [t for t in self._hits.get(token, []) if now - t < window]
            if len(hits) >= limit:
                self._hits[token] = hits
                return False
            hits.append(now)
            self._hits[token] = hits
            # Opportunistic cleanup so memory stays bounded.
            if len(self._hits) > 5000:
                cutoff = now - window
                self._hits = {k: v for k, v in self._hits.items() if v and v[-1] > cutoff}
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()


def client_ip() -> str:
    """Client address for rate limiting.

    X-Forwarded-For is honoured only when ``TRUST_PROXY`` is enabled — a
    client could otherwise spoof the header to rotate identities and bypass
    the login limiter.
    """
    from flask import current_app

    if current_app.config.get("TRUST_PROXY"):
        fwd = request.headers.get("X-Forwarded-For", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.remote_addr or "unknown"


def register_security(app: Flask) -> None:
    @app.context_processor
    def _inject_csrf():
        return {"csrf_token": generate_csrf_token, "CSRF_FIELD": CSRF_FIELD}

    @app.before_request
    def _csrf_and_rate():
        # Admin API + forms are all CSRF protected; public booking POSTs too.
        if request.method not in SAFE_METHODS and not request.path.startswith(CSRF_EXEMPT_PREFIXES):
            blocked = validate_csrf()
            if blocked is not None:
                return blocked
            # CSRF OK

        if request.path.startswith("/api/") or request.path.startswith("/admin"):
            limit = int(app.config.get("RATE_LIMIT_API_PER_MINUTE", 120))
            if not limiter.allow("api", client_ip(), limit):
                msg = "too_many_requests"
                if request.path.startswith("/api/"):
                    return jsonify({"error": msg}), 429
                return jsonify({"error": msg}), 429
        return None

    @app.after_request
    def _security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data: https:; "
            "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
            "media-src 'self' data:; connect-src 'self'",
        )
        # Never let a browser serve a cached HTML form: its embedded CSRF
        # token would be stale and the submit would be rejected. Assets keep
        # their normal caching. Also clears back/forward-cache restoring an
        # old form after a logout or a dropped session cookie.
        if not request.path.startswith("/static/") and response.mimetype == "text/html":
            response.headers.setdefault("Cache-Control", "no-store")
        if app.config.get("SESSION_COOKIE_SECURE"):
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return response
