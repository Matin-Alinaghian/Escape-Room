"""Database access helpers (SQLite).

Design notes
------------
* One connection per request (Flask ``g``), ``row_factory = sqlite3.Row``.
* ``PRAGMA foreign_keys`` and ``journal_mode=WAL`` are applied per connection.
* Writes use ``BEGIN IMMEDIATE`` so concurrent booking attempts serialise on
  the single SQLite write lock — this is the concurrency-safety mechanism that
  prevents double bookings and overbooking (see services/availability.py).
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

import click
from flask import current_app, g

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


class Row(sqlite3.Row):
    """A ``sqlite3.Row`` that also behaves like a read-only dict.

    View code reaches for ``row.get("col", default)`` constantly; the stdlib
    row type has no ``get``, so every such call would raise AttributeError.
    Adding it here fixes the whole codebase in one place.
    """

    def get(self, key, default=None):
        try:
            return self[key]
        except (IndexError, KeyError):
            return default

    def __contains__(self, key) -> bool:  # pragma: no cover - trivial
        if not isinstance(key, str):
            return super().__contains__(key)
        return key in self.keys()


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=15, isolation_level=None)
    conn.row_factory = Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 15000")
    return conn


def get_db() -> sqlite3.Connection:
    """Return (and cache) the connection for the current request."""
    if "db" not in g:
        path = current_app.config["DATABASE_FILE"]
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        g.db = _connect(str(path))
    return g.db


def close_db(_exc=None) -> None:
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


@contextmanager
def write_txn(conn: sqlite3.Connection | None = None):
    """Exclusive write transaction (BEGIN IMMEDIATE)."""
    conn = conn or get_db()
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def query(sql: str, args: tuple | dict = (), *, one: bool = False):
    """Run a SELECT and return Row(s)."""
    cur = get_db().execute(sql, args)
    rows = cur.fetchall()
    cur.close()
    if one:
        return rows[0] if rows else None
    return rows


def query_one(sql: str, args: tuple | dict = ()):
    return query(sql, args, one=True)


def execute(sql: str, args: tuple | dict = ()) -> sqlite3.Cursor:
    """Run a write statement on its own transaction."""
    conn = get_db()
    conn.execute("BEGIN IMMEDIATE")
    try:
        cur = conn.execute(sql, args)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return cur


def executescript(sql: str) -> None:
    get_db().executescript(sql)


def now_iso() -> str:
    """UTC timestamp in the canonical storage format."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def table_exists(name: str) -> bool:
    return (
        query_one(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
        )
        is not None
    )


def column_exists(table: str, column: str) -> bool:
    """Has an existing database picked up a column a later release added?

    The application factory uses this to decide whether the (idempotent)
    migration in :func:`init_db` has to run at startup: a database that is
    missing only a column would otherwise never be told about it.
    """
    if not table_exists(table):
        return False
    return any(
        row["name"] == column for row in query(f"PRAGMA table_info({table})")
    )


def init_db() -> None:
    """Create every table from schema.sql (repeatable / idempotent)."""
    conn = get_db()
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations(version) VALUES ('0001_initial')"
    )
    # ---- incremental migration 0002: customer accounts -------------------
    # Older databases do not carry the customer-login columns; ADD COLUMN is
    # idempotent-guarded by checking the table info first.
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(customers)").fetchall()}
    if "password_hash" not in cols:
        conn.execute("ALTER TABLE customers ADD COLUMN password_hash TEXT")
    if "must_change_pw" not in cols:
        conn.execute(
            "ALTER TABLE customers ADD COLUMN must_change_pw INTEGER NOT NULL DEFAULT 0"
        )
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations(version) VALUES ('0002_customer_accounts')"
    )
    # ---- incremental migration 0003: sign-up verification codes ----------
    # The table itself lives in schema.sql; re-running the script above is
    # harmless (everything is CREATE ... IF NOT EXISTS) and this row just
    # records that an existing database has picked it up.
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations(version) VALUES ('0003_signup_codes')"
    )
    # ---- incremental migration 0004: per-room booking switch ----------------
    # ``scenarios.is_bookable`` lets the administrator close one room for
    # booking without hiding it (or its bookings) from the site.
    scols = {r["name"] for r in conn.execute("PRAGMA table_info(scenarios)").fetchall()}
    if "is_bookable" not in scols:
        conn.execute(
            "ALTER TABLE scenarios ADD COLUMN is_bookable INTEGER NOT NULL DEFAULT 1"
        )
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations(version) VALUES ('0004_room_booking_switch')"
    )
    # ---- incremental migration 0005: slot sold_out marker ------------------
    # When the booking transaction fills the last seats of a session it marks
    # the slot itself ``sold_out``; the CHECK on the live table (from older
    # releases) allows only 'open'/'blocked', so the marker write crashed the
    # booking. SQLite cannot ALTER a CHECK. Rebuild the one table in place
    # (12-column copy with the new constraint, then drop and rename) — the
    # UNIQUE key on (scenario_id, slot_date, start_time) is carried over, and
    # foreign keys are switched off only for the duration of the copy.
    status_check_exists = False
    for index, ddl in conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE name='slots'"
    ).fetchall():
        status_check_exists = "('open','blocked')" in (ddl or "")
    if status_check_exists:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(slots)").fetchall()]
        conn.execute("PRAGMA foreign_keys=OFF")
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "CREATE TABLE slots_new ("
                "id         INTEGER PRIMARY KEY AUTOINCREMENT, "
                "scenario_id INTEGER NOT NULL REFERENCES scenarios(id) ON DELETE CASCADE, "
                "slot_date  TEXT    NOT NULL, "
                "start_time TEXT    NOT NULL, "
                "end_time   TEXT    NOT NULL, "
                "capacity   INTEGER NOT NULL CHECK (capacity > 0), "
                "price      INTEGER NOT NULL DEFAULT 0 CHECK (price >= 0), "
                "status     TEXT    NOT NULL DEFAULT 'open' "
                "           CHECK (status IN ('open','blocked','sold_out')), "
                "generated  INTEGER NOT NULL DEFAULT 1, "
                "updated_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')), "
                "UNIQUE (scenario_id, slot_date, start_time))"
            )
            col_list = ", ".join(cols)
            conn.execute(
                f"INSERT INTO slots_new ({col_list}) SELECT {col_list} FROM slots"
            )
            conn.execute("DROP TABLE slots")
            conn.execute("ALTER TABLE slots_new RENAME TO slots")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_slots_lookup "
                "ON slots(scenario_id, slot_date, status)"
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations(version) VALUES ('0005_slot_sold_out')"
    )


def register_commands(app) -> None:  # pragma: no cover - CLI wiring
    @app.cli.command("init-db")
    def init_db_command() -> None:
        """Create all tables."""
        init_db()
        click.echo("Database initialised.")

    @app.cli.command("init-admin")
    @click.option("--username", prompt=True)
    @click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
    @click.option("--name", default="مدیر اصلی")
    def init_admin_command(username: str, password: str, name: str) -> None:
        """Create (or report) the single super administrator account."""
        from app.auth import hash_password

        init_db()
        existing = query_one("SELECT id FROM admins WHERE role='super'")
        if existing:
            click.echo("A super administrator already exists; nothing changed.")
            return
        if len(password) < 10:
            raise click.ClickException("Password must be at least 10 characters.")
        execute(
            "INSERT INTO admins(username,password_hash,display_name,role) "
            "VALUES (?,?,?,'super')",
            (username, hash_password(password), name),
        )
        click.echo(f"Super administrator '{username}' created.")

    @app.cli.command("seed")
    def seed_command() -> None:
        """Insert demo scenarios, schedules and settings (idempotent)."""
        from app.seed import seed

        init_db()
        seed()
        click.echo("Seed data inserted.")
