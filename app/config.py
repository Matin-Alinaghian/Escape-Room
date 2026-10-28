"""Application configuration loaded from environment / .env file."""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
INSTANCE_DIR = BASE_DIR / "instance"


def _load_dotenv() -> None:
    """Tiny .env loader (stdlib fallback if python-dotenv is unavailable)."""
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv  # type: ignore

        load_dotenv(env_path)
        return
    except Exception:
        pass
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv()


def _bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def default_port() -> int:
    """Port the development server binds when none is given on the CLI.

    ``PORT`` is honoured for hosting platforms, but ``0`` means "any free
    port" to the OS and is what some sandboxes and CI images export. Silently
    binding a random port there makes it look like the server never came up,
    so a missing, zero, negative, too-large or unparsable value falls back to
    the documented default.
    """
    try:
        port = int((os.environ.get("PORT") or "").strip())
    except ValueError:
        return 5000
    return port if 1 <= port <= 65535 else 5000


def _secret_key() -> str:
    """Session signing key — never a publicly known constant.

    Precedence: ``FLASK_SECRET_KEY`` env → persisted random key in the
    instance directory → freshly generated and persisted. Refuses to boot in
    production without an explicit key so a forgotten env var can never turn
    into forgeable session cookies.
    """
    key = (os.environ.get("FLASK_SECRET_KEY") or "").strip()
    if key:
        return key
    if (os.environ.get("FLASK_ENV") or "development") == "production":
        raise RuntimeError(
            "FLASK_SECRET_KEY is not set. Refusing to start in production with "
            "an unsigned session secret. Generate one with: "
            "python -c \"import secrets;print(secrets.token_hex(32))\""
        )
    key_file = INSTANCE_DIR / "secret_key"
    try:
        if key_file.exists():
            stored = key_file.read_text(encoding="utf-8").strip()
            if stored:
                return stored
        import secrets as _secrets

        generated = _secrets.token_hex(32)
        key_file.write_text(generated, encoding="utf-8")
        try:
            os.chmod(key_file, 0o600)
        except OSError:  # Windows without POSIX perms — best effort.
            pass
        return generated
    except OSError:
        # Read-only filesystem: per-process key (sessions reset on restart)
        # is still safer than a known constant.
        import secrets as _secrets

        return _secrets.token_hex(32)


class Config:
    """Flask configuration object."""

    SECRET_KEY = _secret_key()
    ENV = os.environ.get("FLASK_ENV") or "development"
    DEBUG = ENV != "production"
    # Trust X-Forwarded-* only when explicitly behind a reverse proxy;
    # otherwise clients could spoof their IP and dodge rate limits.
    TRUST_PROXY = _bool("TRUST_PROXY", False)

    DATABASE_PATH = os.environ.get("DATABASE_PATH") or str(INSTANCE_DIR / "escape_room.db")
    SITE_URL = (os.environ.get("SITE_URL") or "http://localhost:5000").rstrip("/")
    ALLOWED_ORIGINS = [
        o.strip()
        for o in (os.environ.get("ALLOWED_ORIGINS") or "").split(",")
        if o.strip()
    ]
    TIMEZONE = os.environ.get("TIMEZONE") or "Asia/Tehran"

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SECURE = _bool("SESSION_COOKIE_SECURE", False)
    SESSION_COOKIE_SAMESITE = (os.environ.get("SESSION_COOKIE_SAMESITE") or "Lax").title()
    PERMANENT_SESSION_LIFETIME = _int("SESSION_LIFETIME_HOURS", 12) * 3600

    MAX_IMAGE_BYTES = _int("MAX_IMAGE_BYTES", 5 * 1024 * 1024)
    MAX_VIDEO_BYTES = _int("MAX_VIDEO_BYTES", 40 * 1024 * 1024)
    MAX_CONTENT_LENGTH = max(MAX_IMAGE_BYTES, MAX_VIDEO_BYTES)

    RATE_LIMIT_LOGIN_PER_MINUTE = _int("RATE_LIMIT_LOGIN_PER_MINUTE", 5)
    RATE_LIMIT_API_PER_MINUTE = _int("RATE_LIMIT_API_PER_MINUTE", 120)

    # Customer sign-up: phone number + one-time SMS code --------------------
    SIGNUP_CODE_TTL_SECONDS = _int("SIGNUP_CODE_TTL_SECONDS", 120)
    SIGNUP_VERIFIED_TTL_SECONDS = _int("SIGNUP_VERIFIED_TTL_SECONDS", 900)
    SIGNUP_RESEND_COOLDOWN_SECONDS = _int("SIGNUP_RESEND_COOLDOWN_SECONDS", 45)
    SIGNUP_MAX_ATTEMPTS = _int("SIGNUP_MAX_ATTEMPTS", 5)
    # While SMS delivery is off (SMS_ENABLED=0) the verification code is shown
    # on the page instead of being texted — otherwise nobody could ever finish
    # signing up on a machine without a gateway. Ignored as soon as a real
    # provider is configured, so a live site never leaks the code.
    SIGNUP_SHOW_CODE = _bool("SIGNUP_SHOW_CODE", True)

    # SMS -------------------------------------------------------------------
    SMS_ENABLED = _bool("SMS_ENABLED", False)
    SMS_PROVIDER = os.environ.get("SMS_PROVIDER") or "mock"
    SMS_API_KEY = os.environ.get("SMS_API_KEY") or ""
    SMS_API_SECRET = os.environ.get("SMS_API_SECRET") or ""
    SMS_SENDER = os.environ.get("SMS_SENDER") or ""
    SMS_BASE_URL = os.environ.get("SMS_BASE_URL") or ""

    # Payments --------------------------------------------------------------
    PAYMENT_ENABLED = _bool("PAYMENT_ENABLED", False)
    PAYMENT_PROVIDER = os.environ.get("PAYMENT_PROVIDER") or "mock"
    PAYMENT_MERCHANT_ID = os.environ.get("PAYMENT_MERCHANT_ID") or ""
    PAYMENT_API_KEY = os.environ.get("PAYMENT_API_KEY") or ""
    PAYMENT_BASE_URL = os.environ.get("PAYMENT_BASE_URL") or ""

    # Uploaded files live inside the package so they are served statically.
    # UPLOAD_DIR is overridable so the test suite never writes into the repo.
    UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR") or (BASE_DIR / "app" / "static" / "uploads"))
    SCENARIO_UPLOAD_DIR = UPLOAD_DIR / "scenarios"
    MEDIA_UPLOAD_DIR = UPLOAD_DIR / "media"

    ALLOWED_IMAGE_EXT = {"jpg", "jpeg", "png", "webp", "gif"}
    ALLOWED_VIDEO_EXT = {"mp4", "webm", "ogg"}

    # Security: iterations for password hashing (Werkzeug pbkdf2/scrypt).
    PASSWORD_HASH_METHOD = "scrypt:32768:16:1"

    @property
    def database_file(self) -> Path:
        path = Path(self.DATABASE_PATH)
        if not path.is_absolute():
            path = BASE_DIR / path
        return path
