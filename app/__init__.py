"""Flask application factory."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from flask import Flask, render_template, request, send_from_directory

from app.config import BASE_DIR, Config
from app.db import (
    close_db,
    column_exists,
    get_db,
    init_db,
    register_commands,
    table_exists,
)


def create_app(config_object: type = Config) -> Flask:
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )
    app.config.from_object(config_object)
    # app/db.py resolves the SQLite file through this single key. Resolve it once,
    # here, so every module agrees on the path (and relative paths resolve
    # against the project root rather than the process working directory).
    # `database_file` is a property, so it must be read off an instance.
    _db_path = Path(str(config_object().database_file))
    app.config["DATABASE_FILE"] = str(_db_path)
    Path(_db_path).parent.mkdir(parents=True, exist_ok=True)

    # Directories used by the upload pipeline.
    for key in ("UPLOAD_DIR", "SCENARIO_UPLOAD_DIR", "MEDIA_UPLOAD_DIR"):
        Path(app.config[key]).mkdir(parents=True, exist_ok=True)

    # Serve uploads explicitly instead of relying on UPLOAD_DIR living inside the
    # package's static folder. This keeps the files reachable when UPLOAD_DIR is
    # moved off disk (a mounted volume, a container mount, a test tmpdir), which
    # Flask's built-in /static route cannot do.
    @app.route("/static/uploads/<path:filename>")
    def uploaded_file(filename: str):
        return send_from_directory(
            app.config["UPLOAD_DIR"], filename, max_age=86400, conditional=True
        )

    # Timezone used by the booking engine.
    from app.services import availability

    availability.set_timezone(app.config.get("TIMEZONE", "Asia/Tehran"))

    # Numerals: the admin panel prints numbers as numbers (Latin digits), the
    # public site keeps its Persian ones. The choice is made once per request —
    # not once per worker thread — so a reused thread can never carry the
    # previous request's style into the next page.
    @app.before_request
    def _digit_style():
        from app.jalali import use_latin_digits

        use_latin_digits(request.path.startswith("/admin"))

    @app.teardown_request
    def _reset_digit_style(_exc=None):
        # The style belongs to the request, never to the worker thread: a thread
        # that has just rendered an admin page must not render the next public
        # page with Latin numerals.
        from app.jalali import use_latin_digits

        use_latin_digits(False)

    # ----- database ---------------------------------------------------------
    app.teardown_appcontext(close_db)
    register_commands(app)

    with app.app_context():
        # init_db() is fully idempotent (every statement is CREATE ... IF NOT
        # EXISTS plus a guarded ADD COLUMN), so running it again when something
        # newer is still missing is how an existing database picks up schema
        # additions without a separate migration command. Each check below
        # stands for one release's addition (table, table, column).
        if (
            not table_exists("scenarios")
            or not table_exists("signup_codes")
            or not column_exists("scenarios", "is_bookable")
        ):
            init_db()
        # Keep notification templates available from the first request.
        try:
            from app.services.notifications import ensure_templates

            ensure_templates()
        except Exception:
            pass

    # ----- security ---------------------------------------------------------
    from app.security import register_security

    register_security(app)

    # ----- performance ------------------------------------------------------
    # The dev/standalone server sends neither compression nor long-lived cache
    # headers. Assets are versioned by `asset_v` (file mtime), so any URL with
    # ?v= can be cached forever; everything text is gzipped on the way out.
    import gzip as _gzip

    _COMPRESS_MIMES = {
        "text/html",
        "text/css",
        "text/plain",
        "text/xml",
        "application/javascript",
        "application/json",
        "application/xml",
        "image/svg+xml",
    }

    _GZIP_CACHE = {}  # (etag, size) -> compressed bytes for static assets

    @app.before_request
    def _gzip_etag_conditional():
        # We stamp a `-gzip` suffix onto ETags, so strip it from conditional
        # requests before send_file's make_conditional() compares them,
        # otherwise gzip clients would never get a 304.
        inm = request.environ.get("HTTP_IF_NONE_MATCH")
        if inm and "-gzip" in inm:
            request.environ["HTTP_IF_NONE_MATCH"] = inm.replace("-gzip", "")

    @app.after_request
    def _performance_headers(response):
        # Long-lived cache only for versioned assets (cache-busted by mtime).
        if request.path.startswith("/static/"):
            if "v=" in request.query_string.decode("utf-8", "ignore"):
                response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            elif response.headers.get("Cache-Control") in (None, "no-cache"):
                response.headers["Cache-Control"] = "public, max-age=3600"

        # Gzip text payloads once, if the client asked and it's worth it.
        if (
            response.status_code == 200
            and not response.headers.get("Content-Encoding")
            and response.mimetype in _COMPRESS_MIMES
            and "gzip" in (request.headers.get("Accept-Encoding") or "")
        ):
            payload = None
            if not response.is_streamed:
                payload = response.get_data()
            elif response.direct_passthrough and request.path.startswith("/static/"):
                # Static files arrive as a streaming FileWrapper (direct
                # passthrough): drain it once -- the file is read anyway --
                # so the bytes can be compressed and cached by ETag.
                wrapper = response.response
                response.direct_passthrough = False
                payload = b"".join(wrapper)
                if hasattr(wrapper, "close"):
                    try:
                        wrapper.close()
                    except Exception:
                        pass
            if payload is not None and len(payload) >= 1024:
                # Cache compressed bytes ONLY for static assets: they carry a
                # real ETag (and are cache-busted by ?v=). Dynamic HTML has no
                # ETag and two renders often have the exact same byte length
                # (a CSRF token is always 43 chars), so caching it would replay
                # one render -- with someone else's CSRF token / flash messages
                # -- to every later request.
                key = None
                if request.path.startswith("/static/") and response.headers.get("ETag"):
                    key = (response.headers.get("ETag"), len(payload))
                compressed = _GZIP_CACHE.get(key) if key is not None else None
                if compressed is None:
                    compressed = _gzip.compress(payload, 6)
                    if len(compressed) < len(payload):
                        if key is not None:
                            if len(_GZIP_CACHE) > 512:
                                _GZIP_CACHE.clear()
                            _GZIP_CACHE[key] = compressed
                    else:
                        compressed = None  # not worth compressing
                if compressed is not None:
                    response.set_data(compressed)
                    response.headers["Content-Encoding"] = "gzip"
                    response.headers["Content-Length"] = str(len(compressed))
                    vary = response.headers.get("Vary")
                    if not vary:
                        response.headers["Vary"] = "Accept-Encoding"
                    elif "Accept-Encoding" not in vary:
                        response.headers["Vary"] = vary + ", Accept-Encoding"
                    # ETag must differ per representation.
                    etag = response.headers.get("ETag")
                    if etag and not etag.endswith("-gzip"):
                        response.headers["ETag"] = '"' + etag.strip('"') + '-gzip"'
        return response

    # ----- blueprints -------------------------------------------------------
    from app.blueprints.public import bp as public_bp
    from app.blueprints.admin import bp as admin_bp
    from app.blueprints.api import bp as api_bp
    from app.blueprints.account import bp as account_bp

    app.register_blueprint(public_bp)
    app.register_blueprint(account_bp)
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(api_bp, url_prefix="/api")

    # ----- template helpers -------------------------------------------------
    _register_filters(app)
    _register_context(app)

    # ----- error pages ------------------------------------------------------
    @app.errorhandler(404)
    def not_found(_e):
        return render_template("errors/404.html"), 404

    @app.errorhandler(403)
    def forbidden(_e):
        return render_template("errors/403.html"), 403

    @app.errorhandler(500)
    def server_error(_e):
        return render_template("errors/500.html"), 500

    return app


def _register_filters(app: Flask) -> None:
    from app.jalali import fa_digits, format_jalali

    @app.template_filter("fa")
    def fa(value):  # noqa: ANN001
        if value is None:
            return ""
        return fa_digits(value)

    @app.template_filter("jdate")
    def jdate(value, weekday: bool = False):  # noqa: ANN001
        if not value:
            return ""
        from datetime import date, datetime

        if isinstance(value, str):
            try:
                value = datetime.strptime(value[:10], "%Y-%m-%d").date()
            except ValueError:
                return value
        if isinstance(value, datetime):
            value = value.date()
        if isinstance(value, date):
            return format_jalali(value, with_weekday=weekday)
        return value

    @app.template_filter("money")
    def money(value):  # noqa: ANN001
        try:
            return fa_digits(f"{int(value):,}") + " تومان"
        except (TypeError, ValueError):
            return ""

    @app.template_filter("stars")
    def stars(value, label: str = "امتیاز", maximum: int = 5):  # noqa: ANN001
        """Star meter for an average rating, filled by exact percentage.

        An average of 4.75 is not five full stars: the lit copy of the row is
        clipped to 95% of its width, so the last star stays three quarters
        lit. Whole-star markup is still used for a single review's rating,
        which is always an integer.
        """
        from markupsafe import Markup, escape

        try:
            top = max(1, int(maximum or 5))
        except (TypeError, ValueError):
            top = 5
        try:
            score = float(value or 0)
        except (TypeError, ValueError):
            score = 0.0
        score = min(max(score, 0.0), float(top))

        # Trailing zeros are dropped so 4.50 reads as «۴.۵» and 4.00 as «۴».
        readable = f"{score:.2f}".rstrip("0").rstrip(".")
        aria = f"{label} {fa_digits(readable)} از {fa_digits(top)}"
        glyphs = "★" * top
        return Markup(
            f'<span class="stars star-meter" role="img" style="--pct:{score / top * 100:.4f}%" '
            f'aria-label="{escape(aria)}">'
            f'<span class="star-track" aria-hidden="true">{glyphs}</span>'
            f'<span class="star-fill" aria-hidden="true">{glyphs}</span>'
            "</span>"
        )

    @app.template_filter("iso_dt")
    def iso_dt(value):  # noqa: ANN001
        """A stored timestamp, on the shop's clock.

        Every ``created_at``/``updated_at`` in this database is written in UTC
        (``db.now_iso`` and the column default). The timestamp used to be
        printed raw, so the panel showed "ثبت ۱۱:۰۷" for a booking made at
        ۱۴:۳۷ in Tehran. It is converted to the business timezone first.
        """
        if not value:
            return ""
        try:
            from datetime import datetime

            from app.services.availability import to_business_time

            dt = datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")
            local = to_business_time(dt)
            return format_jalali(local) + " " + fa_digits(local.strftime("%H:%M"))
        except ValueError:
            return value

    @app.template_filter("status_fa")
    def status_fa(value):  # noqa: ANN001
        from app.services.availability import STATUS_LABELS

        return STATUS_LABELS.get(value, value or "")

    @app.template_filter("players")
    def players(minimum, maximum=None):  # noqa: ANN001
        """Render a player range, collapsing a fixed-size room to a single number."""
        if minimum in (None, "") or maximum in (None, ""):
            return fa_digits(minimum or "")
        lo, hi = int(minimum), int(maximum)
        if lo == hi:
            return fa_digits(lo) + " نفر"
        return fa_digits(lo) + " تا " + fa_digits(hi) + " نفر"

    @app.template_filter("nl2br")
    def nl2br(value):  # noqa: ANN001
        """Plain text with its line breaks kept.

        The text is escaped, so a line break that reaches it as the four
        characters ``<br>`` — which is how one arrives from the CMS editor —
        used to be printed literally on the page. It is read as the line break
        it means; every other tag stays inert.
        """
        import re

        from markupsafe import Markup, escape

        text = re.sub(r"(?i)<br\s*/?>", "\n", str(value or ""))
        # Escape first, then swap the breaks in as a plain string and mark the
        # result safe. ``Markup.replace`` escapes its replacement, which is how
        # every line break used to reach the page as the visible text
        # "<br>" instead of a new line.
        escaped = str(escape(text))
        return Markup(escaped.replace("\n", "<br>\n"))

    @app.template_filter("map_url")
    def map_url(value):  # noqa: ANN001
        """Turn an address into a مسیریاب نشان (Neshan) search link.

        Used wherever an address is displayed: footer, contact page and the
        per-room مکان card. The admin can pin the exact location once with the
        ``site.map_url`` setting; only when it is empty is the link derived
        from the address text.
        """
        from app.services.neshan import neshan_search_url
        from app.services.settings import get_setting

        pinned = (get_setting("site.map_url") or "").strip()
        if pinned:
            return pinned
        return neshan_search_url(str(value) if value else "")


def _register_context(app: Flask) -> None:
    from app.services.settings import DEFAULTS, get_nav_links, get_setting, get_socials

    def _asset_version(filename: str) -> str:
        """Cache-buster for static assets: the file's mtime so browsers pick up
        CSS/JS changes immediately instead of serving a stale heuristic-cached copy."""
        try:
            import os

            mtime = os.path.getmtime(os.path.join(app.static_folder, filename))
            return str(int(mtime))
        except OSError:
            return "0"

    def _flat(prefix: str) -> dict:
        """Settings for a namespace, keyed WITHOUT the prefix.

        Templates read ``site.instagram`` for the ``site.instagram`` key, so the
        injected dict must be keyed ``instagram`` — earlier it was keyed with
        the full name and every lookup silently fell back to the default.
        """
        return {k[len(prefix):]: get_setting(k) for k in DEFAULTS if k.startswith(prefix)}

    @app.context_processor
    def inject_globals():
        from app.auth import current_admin
        from app.services.customer_auth import current_customer

        # Only a real row is injected (never a partially built session), so a
        # template can safely branch on `signed_in_customer`.
        try:
            signed_in_customer = current_customer()
        except Exception:  # never break a render because the session is stale
            signed_in_customer = None

        return {
            "SITE_URL": app.config.get("SITE_URL", ""),
            "asset_v": _asset_version,
            "site": _flat("site."),
            "brand": _flat("brand."),
            "footer": _flat("footer."),
            "seo": _flat("seo."),
            "nav": get_nav_links(),
            # Only accounts that really exist; a placeholder URL never becomes a
            # footer button (see services.settings.social_handle).
            "socials": get_socials(),
            "current_path": request.path,
            "signed_in_admin": current_admin(),
            "signed_in_customer": signed_in_customer,
            "SMS_ENABLED": app.config.get("SMS_ENABLED", False),
            "PAYMENT_ENABLED": app.config.get("PAYMENT_ENABLED", False),
            "sound": {
                "enabled": bool(get_setting("sound.enabled", True)),
                "volume": get_setting("sound.volume", 0.18),
            },
        }


def _is_running_under_test() -> bool:
    return "pytest" in sys.modules or "unittest" in sys.modules


if __name__ == "__main__":  # pragma: no cover
    from app.config import default_port

    application = create_app()
    application.run(host="127.0.0.1", port=default_port(), debug=True)
