#!/usr/bin/env python
"""Application entry point and management commands.

Usage
-----
    python manage.py run                 # development server
    python manage.py init-db             # create tables
    python manage.py init-admin          # one-time super administrator setup
    python manage.py seed                # demo scenarios + schedules + content
    python manage.py create-secondary    # create a secondary administrator
    python manage.py backup              # copy the database to ./backups
"""
from __future__ import annotations

import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

import click
from flask.cli import FlaskGroup

from app import create_app
from app.config import default_port

BASE = Path(__file__).resolve().parent


def _app():
    return create_app()


cli = FlaskGroup(add_default_commands=False, create_app=_app)


@cli.command("run")
@click.option("--host", default="127.0.0.1")
@click.option("--port", default=default_port, type=int)
@click.option("--debug/--no-debug", default=True)
def run_cmd(host: str, port: int, debug: bool) -> None:
    """Run the development server.

    Flask sets FLASK_RUN_FROM_CLI while a CLI command is executing and makes
    app.run() a silent no-op in that case, so clear the flag first — otherwise
    this command "succeeds" without ever binding a port.
    """
    os.environ.pop("FLASK_RUN_FROM_CLI", None)
    application = _app()
    if application.config.get("ENV") == "production" and debug:
        click.echo("Production environment: forcing --no-debug (the Werkzeug debugger is an RCE risk).")
        debug = False
    application.config["TEMPLATES_AUTO_RELOAD"] = True
    application.run(host=host, port=port, debug=debug, use_reloader=False)


@cli.command("init-admin")
@click.option("--username", prompt=True)
@click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
@click.option("--name", default="مدیر اصلی")
def init_admin_cmd(username: str, password: str, name: str) -> None:
    """Create the single super administrator (one-time, never overwrites)."""
    from app.auth import hash_password
    from app.db import execute, init_db, query_one

    init_db()
    if query_one("SELECT id FROM admins WHERE role='super'"):
        click.echo("A super administrator already exists — refusing to modify it.")
        sys.exit(1)
    if len(password) < 10:
        raise click.ClickException("Password must be at least 10 characters.")
    execute(
        "INSERT INTO admins(username, password_hash, display_name, role) VALUES (?,?,?,'super')",
        (username.strip(), hash_password(password), name),
    )
    click.echo(f"Super administrator '{username}' created.")


@cli.command("reset-password")
@click.option("--username", prompt=True)
@click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
def reset_password_cmd(username: str, password: str) -> None:
    """Reset an administrator's password (recovery when everyone is locked out)."""
    from app.auth import change_password
    from app.db import init_db, query_one
    from app.services.audit import log_action

    init_db()
    row = query_one("SELECT id, role FROM admins WHERE username=?", (username.strip(),))
    if row is None:
        raise click.ClickException(f"No administrator with username '{username}'.")
    if len(password) < 10:
        raise click.ClickException("Password must be at least 10 characters.")
    change_password(row["id"], password)
    log_action("admin.reset_password.cli", f"reset via CLI for {username}",
               entity_type="admin", entity_id=row["id"], admin_name="cli")
    click.echo(f"Password reset for '{username}' ({row['role']}).")


@cli.command("create-secondary")
@click.option("--username", prompt=True)
@click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
@click.option("--name", default="مدیر")
@click.option("--permissions", default="", help="Comma separated permission keys")
def create_secondary_cmd(username: str, password: str, name: str, permissions: str) -> None:
    """Create a secondary administrator with an explicit permission set."""
    from app.auth import hash_password
    from app.db import execute, init_db, query_one
    from app.permissions import ALL_PERMISSIONS

    init_db()
    if not query_one("SELECT id FROM admins WHERE role='super'"):
        raise click.ClickException("Create the super administrator first.")
    if query_one("SELECT id FROM admins WHERE username=?", (username.strip(),)):
        raise click.ClickException("That username is already taken.")

    granted = [p.strip() for p in permissions.split(",") if p.strip()]
    unknown = [p for p in granted if p not in ALL_PERMISSIONS]
    if unknown:
        raise click.ClickException(f"Unknown permissions: {', '.join(unknown)}")

    cur = execute(
        "INSERT INTO admins(username, password_hash, display_name, role, must_change_pw) "
        "VALUES (?,?,?, 'secondary', 1)",
        (username.strip(), hash_password(password), name),
    )
    admin_id = cur.lastrowid
    for perm in granted:
        execute(
            "INSERT OR IGNORE INTO admin_permissions(admin_id, permission) VALUES (?,?)",
            (admin_id, perm),
        )
    click.echo(f"Secondary administrator '{username}' created with {len(granted)} permissions.")


@cli.command("backup")
def backup_cmd() -> None:
    """Copy the SQLite database into ./backups (hot backup via WAL checkpoint)."""
    from app.db import get_db, query_one

    out_dir = BASE / "backups"
    out_dir.mkdir(exist_ok=True)
    conn = get_db()
    conn.execute("PRAGMA wal_checkpoint(FULL)")
    version = query_one("SELECT version FROM schema_migrations ORDER BY applied_at LIMIT 1")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = out_dir / f"escape_room_{stamp}.db"
    shutil.copy2(app_config_path(), dest)
    click.echo(f"Backup written to {dest} (schema {version['version'] if version else 'unknown'})")


def app_config_path() -> Path:
    app = _app()
    return Path(app.config["DATABASE_FILE"])


@cli.command("doctor")
def doctor_cmd() -> None:
    """Report environment, database and integration status."""
    app = _app()
    from app.db import query_one, table_exists
    from app.services.notifications import sms_enabled
    from app.services.payments import payment_enabled

    click.echo(f"Python      : {sys.version.split()[0]}")
    click.echo(f"Flask       : {app.__version__ if hasattr(app, '__version__') else '3.x'}")
    click.echo(f"Database    : {app.config['DATABASE_FILE']}")
    click.echo(f"Tables      : {'ok' if table_exists('reservations') else 'run init-db'}")
    if table_exists("admins"):
        has_super = query_one("SELECT id FROM admins WHERE role='super'") is not None
        click.echo(f"Super admin : {'yes' if has_super else 'no'}")
    click.echo(f"SMS enabled : {sms_enabled()}")
    click.echo(f"Payment     : {payment_enabled()}")


if __name__ == "__main__":
    cli()
