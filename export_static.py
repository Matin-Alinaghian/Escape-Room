"""
Export the escape-room site to a standalone static mirror.

Requirements
------------
* Runs against a live Flask app with a populated database.
* Reads public content from the DB (settings, scenarios, gallery images,
  reviews that are published) and rewrites every HTML so it no longer needs
  the DB at runtime.
* Ships the published reviews as a list plus one room-filtered page per room;
  a query string cannot survive on a static host, so the room pages link to
  their own file instead of ``/reviews?room=<slug>``.
* Copies all static assets (CSS, JS, images, fonts, uploaded scenario
  photos) into a ``build/static`` tree.
* Keeps the exact same public pages + structure, so the resulting folder
  can be uploaded to any static host (S3, Netlify, GitHub Pages, a standard
  shared host with PHP/HTML support, etc.).

What stays dynamic (not part of the static export)
---------------------------------------------------
* Admin panel (/admin/*) — still requires a real DB + server. Delivered
  separately when you rent a host.
* Booking flow, payments, SMS, reservations — require a server; not
  included here.
* Customer accounts, reviews that are pending moderation.

The login page is exported as a read-only preview so the header's "ورود"
link never leads to a blank page. Pass ``--app-url https://your-app`` to
point the exported login form and other server-only links at the live
Flask app instead.

ีThus the static mirror is a *marketing + brochure* site: home, rooms
list, room detail, rules, about, contact, galleries — all the content
you currently have in the DB, frozen as pure HTML + static assets.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import quote, urljoin

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

HERE = Path(__file__).resolve().parent

# Source: the Flask app tree (assumes we're run from the project root).
APP_ROOT = HERE

# Where the static mirror is written.
DEFAULT_OUTPUT = HERE / "build"

# Map Flask view names to the URL they render. Used to fetch each page
# through Flask's test client (so all template rendering, context
# processors, filters, etc. still run) then post-process the HTML to
# make it self-contained.
PUB_ROUTES = [
    ("/", "index.html"),
    ("/rooms", "rooms/index.html"),
    ("/about", "about/index.html"),
    ("/rules", "rules/index.html"),
    ("/contact", "contact/index.html"),
    # Published reviews. Every room also gets its own filtered list below, so
    # "همه نظرات این اتاق" keeps meaning one room in the mirror too.
    ("/reviews", "reviews/index.html"),
    # The login page is exported too, so the header's "ورود" link never
    # dead-ends on a blank page. Without --app-url it renders in read-only
    # "static preview" mode; with it, the form posts to the live app.
    ("/login", "login/index.html"),
]

# Routes that only exist on the Flask server. When --app-url is given, links
# and form actions pointing at these are rewritten to that host so a visitor
# who clicks them from the static mirror still reaches a working page.
APP_ONLY_PREFIXES = (
    "/login",
    "/register",
    "/account",
    "/forgot-password",
    "/book",
    "/admin",
)

# Room-detail pages are generated per slug. We discover them from the DB.
# Other dynamic public pages (e.g. /rooms/<slug>) are handled below.


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _walk_static(src: Path, dst: Path) -> int:
    """Copy src/* (recursively) into dst/, preserving structure.

    Returns the number of files copied.
    """
    count = 0
    for root, dirs, files in os.walk(src):
        rel = Path(root).relative_to(src)
        dest_dir = dst / rel
        dest_dir.mkdir(parents=True, exist_ok=True)
        for name in files:
            src_path = Path(root) / name
            dst_path = dest_dir / name
            # Skip zero-byte placeholders like .gitkeep.
            if src_path.stat().st_size == 0:
                continue
            shutil.copy2(src_path, dst_path)
            count += 1
    return count


def _rewrite_html(
    html: str,
    base_url: str,
    absolute_static: bool,
    app_url: str = "",
    rel_prefix: str = "",
) -> str:
    """Make a rendered HTML page self-contained enough for static hosting.

    * Converts ``/static/...`` URLs to relative paths when absolute_static
      is False (so the mirror works regardless of where it's uploaded),
      otherwise keeps them absolute under ``base_url``.
    * Rewrites any ``href``/``src``/``srcset`` that points at the Flask
      app's own static route so it points at the copied asset.
    * ``rel_prefix`` ("./" at the root, "../" one level deep) keeps those
      relative paths correct for pages written into sub-folders such as
      ``rooms/<slug>.html``.
    * When ``app_url`` is set, points server-only links (login, account,
      booking, admin) at the live Flask app instead of a 404 in the mirror.
    * Without ``app_url``, booking links fall back to the exported login page:
      a reservation needs an account and only the Flask app can sign anyone
      in, so ``/book/<slug>`` has no static counterpart to point at.
    """
    out = html

    # Flask renders static paths as "/static/..." (absolute). Convert to
    # relative so the site works regardless of where it's hosted.
    if not absolute_static:
        # /static/foo.css -> static/foo.css (or ../static/foo.css)
        p = rel_prefix
        out = out.replace('href="/static/', f'href="{p}static/')
        out = out.replace('src="/static/', f'src="{p}static/')
        out = out.replace('srcset="/static/', f'srcset="{p}static/')

    # /reviews is part of the mirror, so its links stay inside the export — the
    # query-string form (?room=, ?sort=, ?page=) is handled where the per-room
    # pages are written, since only there is the room's slug known.
    out = out.replace('href="/reviews"', f'href="{rel_prefix}reviews/index.html"')

    if app_url:
        app_url = app_url.rstrip("/")
        for prefix in APP_ONLY_PREFIXES:
            for attr in ('href="', 'action="'):
                out = out.replace(f'{attr}{prefix}"', f'{attr}{app_url}{prefix}"')
    else:
        out = re.sub(
            r'href="/book/[^"]*"',
            f'href="{rel_prefix}login/index.html"',
            out,
        )

    # Keep absolute asset URLs intact for the mirror's own index (they
    # will be served under base_url).
    return out


def _slug_from_id(scenario_id: int, db_execute) -> str:
    row = db_execute(
        "SELECT slug FROM scenarios WHERE id=? AND status='published'", (scenario_id,)
    )
    if not row:
        return ""
    return row[0]


def _discover_room_slugs(db_execute) -> list[tuple[str, str]]:
    """Return [(slug, title), ...] for every published scenario."""
    rows = db_execute(
        "SELECT slug, title FROM scenarios WHERE status='published' ORDER BY sort_order, id"
    )
    return [(r["slug"], r["title"]) for r in rows]


# ---------------------------------------------------------------------------
# Main export
# ---------------------------------------------------------------------------

def export(
    app,
    output_dir: Path,
    static_source: Path | None = None,
    absolute_static: bool = False,
    base_url: str = "",
    app_url: str = "",
) -> Path:
    """Render every public page and write a static mirror to ``output_dir``.

    Parameters
    ----------
    app:
        A Flask application instance (already created, DB connected).
    output_dir:
        Destination folder. Will be created / wiped.
    static_source:
        Folder to copy static assets from. Defaults to app.static_folder.
    absolute_static:
        If False (default), rewrite ``/static/...`` to relative ``static/...``
        so the mirror is portable. If True, keep absolute paths under base_url.
    base_url:
        Optional base URL prefix (e.g. "https://example.com") used for
        canonical links, OG images, etc. Only affects links that the Flask
        app itself renders as absolute.
    """
    output_dir = Path(output_dir).resolve()
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)

    static_source = static_source or Path(app.static_folder)
    if not static_source.is_dir():
        raise SystemExit(f"static source not found: {static_source}")

    # Copy all public static assets (CSS, JS, images, fonts, uploaded photos).
    copied = _walk_static(static_source, output_dir / "static")
    print(f"Coped {copied} static files into build/static/")

    # ------------------------------------------------------------------
    # 1. Pre-render: fetch the room list to know which room pages exist.
    # ------------------------------------------------------------------
    with app.test_client() as client:
        # Get published room slugs + ids out of the DB via the app's DB
        # accessor (same code path the views use).
        from app.db import query

        room_slugs = _discover_room_slugs(query)

        # ------------------------------------------------------------------
        # 2. Render the fixed public pages.
        # ------------------------------------------------------------------
        pages = {}
        for rule, filename in PUB_ROUTES:
            url = rule
            if rule == "/login" and not app_url:
                # No live app configured -> export the read-only preview
                # (notice instead of a form that could never submit).
                url = "/login?static=1"
            resp = client.get(url)
            if resp.status_code != 200:
                print(f"WARN: {rule} returned {resp.status_code}, skipping")
                continue
            html = resp.data.decode("utf-8")
            # A page written into a sub-folder (rooms/index.html) needs one
            # "../" per folder, or its stylesheet and images never load.
            depth = len(Path(filename).parts) - 1
            html = _rewrite_html(
                html, base_url, absolute_static, app_url, rel_prefix="../" * depth
            )
            dest = output_dir / filename
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(html, encoding="utf-8")
            pages[rule] = dest
            print(f"Rendered {rule} -> {dest.relative_to(output_dir)}")

        # ------------------------------------------------------------------
        # 3. Render each room detail page.
        # ------------------------------------------------------------------
        for slug, title in room_slugs:
            url = f"/rooms/{slug}"
            resp = client.get(url)
            if resp.status_code != 200:
                print(f"WARN: {url} returned {resp.status_code}, skipping")
                continue
            html = resp.data.decode("utf-8")
            html = _rewrite_html(
                html, base_url, absolute_static, app_url, rel_prefix="../"
            )
            # "همه نظرات این اتاق" carries ?room=<slug>, and a static host drops
            # the query — point the button at the room's own exported list.
            html = html.replace(
                f'href="/reviews?room={slug}"', f'href="../reviews/{slug}.html"'
            )
            dest = output_dir / "rooms" / f"{slug}.html"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(html, encoding="utf-8")
            pages[url] = dest
            print(f"Rendered {url} -> rooms/{slug}.html")

        # ------------------------------------------------------------------
        # 3b. One review list per room. The public page takes ?room=<slug>, so
        #     each room gets the list that a static host cannot build from a
        #     query string.
        # ------------------------------------------------------------------
        for slug, title in room_slugs:
            url = f"/reviews?room={slug}"
            resp = client.get(url)
            if resp.status_code != 200:
                print(f"WARN: {url} returned {resp.status_code}, skipping")
                continue
            html = _rewrite_html(
                resp.data.decode("utf-8"),
                base_url,
                absolute_static,
                app_url,
                rel_prefix="../",
            )
            dest = output_dir / "reviews" / f"{slug}.html"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(html, encoding="utf-8")
            pages[url] = dest
            print(f"Rendered {url} -> reviews/{slug}.html")

    # ------------------------------------------------------------------
    # 4. Write a tiny index.json manifest so a future host can discover
    #    the pages without parsing the HTML tree.
    # ------------------------------------------------------------------
    manifest = {
        "generated_at": __import__("datetime").datetime.now().isoformat(),
        "base_url": base_url,
        "app_url": app_url,
        "pages": [
            {"url": rule, "file": str(dest.relative_to(output_dir))}
            for rule, dest in sorted(pages.items())
        ],
        "static_assets_copied": copied,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Wrote manifest.json with {len(pages)} pages")

    return output_dir


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(
        description="Export the escape-room site to a standalone static mirror."
    )
    p.add_argument(
        "--output",
        "-o",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output folder (default: {DEFAULT_OUTPUT})",
    )
    p.add_argument(
        "--static",
        "-s",
        type=Path,
        help="Explicit static source folder (default: app/static)",
    )
    p.add_argument(
        "--absolute-static",
        action="store_true",
        help="Keep /static/... URLs absolute (for a known domain).",
    )
    p.add_argument(
        "--base-url",
        default="",
        help="Base URL to use for canonical/OG links (e.g. https://escape.example.com).",
    )
    p.add_argument(
        "--app-url",
        default="",
        help=(
            "Live Flask app base URL (e.g. https://panel.example.com). When "
            "given, login/account/booking links in the mirror point there; "
            "otherwise the exported login page is a read-only preview."
        ),
    )
    p.add_argument(
        "--app-module",
        default="app:create_app",
        help="Flask app importable (same format as FLASK_APP).",
    )
    args = p.parse_args(argv)

    # Import the Flask app factory.
    import importlib

    module_name, _, factory_name = args.app_module.partition(":")
    if not module_name or not factory_name:
        print(f"Invalid --app-module: {args.app_module}", file=sys.stderr)
        return 2
    mod = importlib.import_module(module_name)
    factory = getattr(mod, factory_name)
    app = factory()

    # Ensure DB is reachable (same path the app uses).
    from app.config import Config

    cfg = Config()
    if not Path(cfg.DATABASE_PATH).exists():
        print(f"Database not found: {cfg.DATABASE_FILE}", file=sys.stderr)
        print("Run `python manage.py init-db` first.", file=sys.stderr)
        return 1

    with app.app_context():
        out = export(
            app,
            output_dir=args.output,
            static_source=args.static,
            absolute_static=args.absolute_static,
            base_url=args.base_url,
            app_url=args.app_url,
        )
    print(f"\nStatic mirror written to: {out}")
    print(f"Upload the contents of {out} to your host (not the folder itself).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
