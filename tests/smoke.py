"""End-to-end smoke test: walks every route and exercises the real write paths.

Run with:  python -m tests.smoke

Uses a throwaway SQLite file so the developer's own database is never touched.
Exits non-zero if any route or flow fails.
"""
from __future__ import annotations

import io
import os
import re
import sys
import tempfile
import traceback
from pathlib import Path

# Windows consoles default to cp1256, which cannot print the Persian text that
# appears in tracebacks. Force UTF-8 so failures are actually readable.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TMP_DIR = tempfile.mkdtemp(prefix="escape-room-smoke-")
os.environ["DATABASE_PATH"] = str(Path(_TMP_DIR) / "smoke.db")
os.environ["FLASK_SECRET_KEY"] = "smoke-test-secret"
os.environ["FLASK_ENV"] = "testing"
# Uploads go to the throwaway dir too, so admin-upload tests never litter the repo.
os.environ["UPLOAD_DIR"] = str(Path(_TMP_DIR) / "uploads")

from app import create_app  # noqa: E402
from app.auth import hash_password  # noqa: E402
from app.db import execute, init_db, query_one  # noqa: E402

# The account the booking tests sign in with (booking is account-only).
BOOKER_PHONE = "09121112233"
BOOKER_PASSWORD = "Booker12345678"


PUBLIC_ROUTES = [
    "/",
    "/rooms",
    "/rules",
    "/about",
    "/contact",
    "/privacy",
    "/reviews",
    "/sitemap.xml",
    "/robots.txt",
    "/ads.txt",
]

ADMIN_ROUTES = [
    "/admin/",
    "/admin/rooms",
    "/admin/rooms/new",
    "/admin/schedules",
    "/admin/bookings",
    "/admin/customers",
    "/admin/users",
    "/admin/reviews",
    "/admin/content",
    "/admin/content/home",
    "/admin/content/site",
    "/admin/content/pages",
    "/admin/content/seo",
    "/admin/media",
    "/admin/reports",
    "/admin/reports?days=7",
    "/admin/settings",
    "/admin/settings/notifications",
    "/admin/logs",
    "/admin/profile",
]

# Markers from the reference design that must survive template edits.
DESIGN_MARKERS = [
    ("rtl document", 'lang="fa" dir="rtl"'),
    ("escape/room logo", 'class="brand-escape">ESCAPE'),
    # The header's single call to action. It used to be a ticket/buy button;
    # booking is account-only now, so the slot is the account entry point and
    # the assertion follows the current design instead of the retired class.
    ("header account link", 'class="nav-link login-link"'),
    ("hero", 'class="hero-title"'),
    ("hero cta", "btn-primary btn-hero"),
    ("polaroid strip", 'class="polaroid"'),
    ("polaroid captions", "polaroid-caption"),
    ("red thread", 'class="thread"'),
    ("torn divider", 'class="paper-divider"'),
    ("title strokes", 'class="stroke"'),
    ("room cards", 'class="room-card"'),
    ("circular room ring", "room-ring"),
    ("footer socials", 'class="social-circle"'),
]


def build_app():
    app = create_app()
    app.config["TESTING"] = True
    with app.app_context():
        init_db()
        from app import seed

        seed.seed()
        # A second, lower-privileged administrator exercises the role split.
        execute(
            "INSERT OR IGNORE INTO admins(username,password_hash,display_name,role) "
            "VALUES (?,?,?,?)",
            ("staff", hash_password("Staff12345678"), "کارمند", "secondary"),
        )
    return app


def check(client, method, url, label, *, expect=None, **kwargs):
    """Hit a route and assert the status code.

    By default anything below 400 passes (3xx redirects are a normal part of
    these flows). Pass ``expect=404`` for routes that should be missing.
    """
    try:
        response = getattr(client, method)(url, **kwargs)
    except Exception:
        print(f"FAIL  {label:<34} {method.upper()} {url}")
        traceback.print_exc()
        return False
    status = response.status_code
    ok = status == expect if expect is not None else status < 400
    print(f"{'ok  ' if ok else 'FAIL'}  {label:<34} {status}")
    if not ok:
        tail = response.data[-400:].decode("utf-8", "replace")
        print(f"      ...{tail}")
    return ok


def csrf_of(client, url):
    """Fetch a page and pull its CSRF token out of the rendered form."""
    html = client.get(url).data.decode("utf-8", "replace")
    match = re.search(r'name="_csrf_token"\s+value="([^"]+)"', html)
    return match.group(1) if match else ""


def assert_that(condition, label, detail=""):
    """Report a boolean assertion in the suite's print style."""
    print(f"{'ok  ' if condition else 'FAIL'}  {label:<34} {detail or ('yes' if condition else 'no')}")
    return bool(condition)


def main() -> int:
    app = build_app()
    failures = 0

    with app.test_client() as client:
        print("--- public pages ---")
        for url in PUBLIC_ROUTES:
            if not check(client, "get", url, url):
                failures += 1

        print("\n--- homepage design markers ---")
        home_html = client.get("/").data.decode("utf-8", "replace")
        for label, marker in DESIGN_MARKERS:
            ok = marker in home_html
            print(f"{'ok  ' if ok else 'FAIL'}  {label:<34} {'found' if ok else 'MISSING'}")
            if not ok:
                failures += 1

        print("\n--- empty reviews block is hidden (reference layout) ---")
        # No reviews exist yet at this point in the run.
        if "reviews-section" in home_html:
            print("FAIL  empty reviews block rendered on the homepage")
            failures += 1
        else:
            print("ok    no empty reviews block")

        print("\n--- scenario pages ---")
        with app.app_context():
            slugs = [r["slug"] for r in _rows("SELECT slug FROM scenarios WHERE status='published'")]
        for slug in slugs:
            if not check(client, "get", f"/rooms/{slug}", f"room detail: {slug}"):
                failures += 1
            # The booking form no longer lives on the detail page: the page
            # only links to the dedicated booking page.
            detail_html = client.get(f"/rooms/{slug}").data.decode("utf-8", "replace")
            if not assert_that(
                f'="/book/{slug}"' in detail_html and 'class="booking-form"' not in detail_html,
                f"room detail -> booking link: {slug}",
                "linked, no inline form" if f'="/book/{slug}"' in detail_html else "link missing",
            ):
                failures += 1
            # Booking is account-only: a guest is sent to the login page and
            # returns to the very booking page afterwards.
            resp = client.get(f"/book/{slug}")
            location = resp.headers.get("Location", "")
            if not assert_that(
                resp.status_code == 302
                and "/login" in location
                and f"/book/{slug}" in location,
                f"guest booking -> login: {slug}",
                f"{resp.status_code} {location}",
            ):
                failures += 1

        print("\n--- API ---")
        with app.app_context():
            scenario_id = query_one("SELECT id FROM scenarios LIMIT 1")["id"]
            from app.services.availability import business_today, ensure_slots

            ensure_slots(scenario_id, business_today())
            slug = query_one("SELECT slug FROM scenarios WHERE id=?", (scenario_id,))["slug"]
            today = business_today().isoformat()
            future = (business_today()).isoformat()
        if not check(client, "get", "/api/health", "health"):
            failures += 1
        if not check(client, "get", f"/api/scenarios/{slug}/slots?date={today}", "slot availability"):
            failures += 1
        if not check(client, "get", f"/api/scenarios/{slug}/calendar", "calendar"):
            failures += 1
        if not check(client, "get", "/api/reviews/stats", "review stats"):
            failures += 1

        print("\n--- CSRF is enforced ---")
        if not check(client, "post", "/admin/login", "login without token", expect=403,
                     data={"username": "admin", "password": "Admin12345678"}):
            failures += 1
        if not check(client, "post", "/book/" + slug, "booking without token", expect=403,
                     data={"name": "x", "phone": "09120000000"}):
            failures += 1

        print("\n--- auth guard (guests are redirected) ---")
        for url in ("/admin/", "/admin/bookings", "/admin/settings", "/admin/logs"):
            if not check(client, "get", url, f"guest {url}"):
                failures += 1

        print("\n--- admin, signed in ---")
        token = csrf_of(client, "/admin/login")
        if not check(client, "post", "/admin/login", "login",
                     data={"username": "admin", "password": "Admin12345678",
                           "_csrf_token": token},
                     follow_redirects=True):
            failures += 1
        for url in ADMIN_ROUTES:
            if not check(client, "get", url, url):
                failures += 1

        print("\n--- admin: room CRUD ---")
        admin_token = csrf_of(client, "/admin/rooms/new")
        room_data = {
            "_csrf_token": admin_token,
            "title": "اتاق تست",
            "slug": "",
            "short_description": "توضیح کوتاه تست",
            "story": "داستان کامل اتاق تست برای بررسی عملکرد پنل مدیریت.",
            "genre": "وحشت",
            "horror_intensity": "3",
            "difficulty": "4",
            "duration_minutes": "60",
            "min_players": "2",
            "max_players": "4",
            "price_per_person": "300000",
            "price_per_group": "0",
            "discount_percent": "0",
            "address": "تهران",
            "age_restriction": "۱۲ سال",
            "safety": "ایمن",
            "rules": "قوانین تست",
            "faqs": "سؤال تست؟ | پاسخ تست",
            "featured": "1",
            "status": "published",
            "sort_order": "9",
            "seo_title": "تست",
            "seo_description": "توضیح تست",
        }
        if not check(client, "post", "/admin/rooms/new", "create room",
                     data=room_data, follow_redirects=True):
            failures += 1
        with app.app_context():
            created = query_one("SELECT * FROM scenarios WHERE title=?", ("اتاق تست",))
        if created is None:
            print("FAIL  room was not persisted")
            failures += 1
        else:
            if not check(client, "get", f"/admin/rooms/{created['id']}/edit", "edit room form"):
                failures += 1
            if not check(client, "get", f"/admin/rooms/{created['id']}/images", "room gallery"):
                failures += 1
            if not check(client, "get", f"/rooms/{created['slug']}", "new room is public"):
                failures += 1
            if not check(client, "post", f"/admin/rooms/{created['id']}/status",
                         "archive room", data={"_csrf_token": admin_token, "status": "draft"},
                         follow_redirects=True):
                failures += 1

        print("\n--- room gallery is populated ---")
        # The seed must attach images, otherwise the gallery section silently
        # disappears from every room page.
        gallery_room = query_one("SELECT * FROM scenarios WHERE slug='atakh-zendan'")
        if gallery_room is None:
            print("FAIL  seeded room atakh-zendan is missing")
            failures += 1
        else:
            detail_html = client.get(f"/rooms/{gallery_room['slug']}").data.decode("utf-8", "replace")
            img_count = query_one(
                "SELECT count(*) c FROM scenario_images WHERE scenario_id=?", (gallery_room["id"],)
            )["c"]
            if not assert_that(img_count >= 5, "seeded images per room", f"{img_count} rows"):
                failures += 1
            if not assert_that('class="gallery"' in detail_html, "gallery renders on room page"):
                failures += 1
            thumbs = detail_html.count("data-img=")
            if not assert_that(thumbs == img_count, "thumb count matches images", f"{thumbs} thumbs"):
                failures += 1

        print("\n--- fixed-capacity rooms collapse the player range ---")
        # atakh-zendan is seeded 5..5; it must never print "۵ تا ۵".
        if gallery_room is not None:
            zendan_html = client.get("/rooms/atakh-zendan").data.decode("utf-8", "replace")
            if not assert_that("تا ۵" not in zendan_html, "no '۵ تا ۵' range shown"):
                failures += 1
            if not assert_that("۵ نفر" in zendan_html, "fixed room shows '۵ نفر'"):
                failures += 1
        bimaristan_html = client.get("/rooms/atakh-bimaristan").data.decode("utf-8", "replace")
        if not assert_that("۲ تا ۴ نفر" in bimaristan_html, "variable room keeps its range"):
            failures += 1

        print("\n--- admin replaces a room photo end to end ---")
        # The whole point of the gallery is that the admin can swap the stock art
        # for real photography: upload -> re-encode -> thumb -> DB row -> public page.
        if gallery_room is None:
            print("FAIL  cannot test uploads without a room")
            failures += 1
        else:
            def _jpeg_fixture(width: int, height: int, rgb: tuple) -> io.BytesIO:
                from PIL import Image

                buf = io.BytesIO()
                Image.new("RGB", (width, height), rgb).save(buf, "JPEG", quality=90)
                buf.seek(0)
                return buf

            sid = gallery_room["id"]
            imgs_url = f"/admin/rooms/{sid}/images"
            before = query_one(
                "SELECT count(*) c FROM scenario_images WHERE scenario_id=?", (sid,)
            )["c"]

            up_token = csrf_of(client, imgs_url)
            if not assert_that(bool(up_token), "gallery form exposes CSRF token"):
                failures += 1

            resp = client.post(
                imgs_url,
                data={"_csrf_token": up_token, "alt": "تصویر آزمایشی ادمین",
                      "image": (_jpeg_fixture(900, 600, (120, 8, 14)), "admin-upload.jpg")},
                content_type="multipart/form-data",
                follow_redirects=True,
            )
            if not assert_that(resp.status_code < 400, "upload accepted", str(resp.status_code)):
                failures += 1
            else:
                body = resp.data.decode("utf-8", "replace")
                if not assert_that("تصویر بارگذاری شد." in body,
                                   "success flash shown"):
                    failures += 1

            with app.app_context():
                after = query_one(
                    "SELECT count(*) c FROM scenario_images WHERE scenario_id=?", (sid,)
                )["c"]
                newest = query_one(
                    "SELECT * FROM scenario_images WHERE scenario_id=? ORDER BY id DESC LIMIT 1", (sid,)
                )
            if not assert_that(after == before + 1, "image row inserted", f"{before} -> {after}"):
                failures += 1
            elif newest is not None:
                url = newest["path"]
                served = client.get(url)
                if not assert_that(served.status_code == 200, "uploaded file served", url):
                    failures += 1
                if not assert_that(
                    served.mimetype == "image/jpeg", "upload re-encoded to jpeg", served.mimetype
                ):
                    failures += 1
                thumb = client.get(url[:-4] + "_thumb.jpg")
                if not assert_that(thumb.status_code == 200, "thumbnail generated"):
                    failures += 1
                # ...and it must be visible on the public room page.
                pub = client.get(f"/rooms/{gallery_room['slug']}").data.decode("utf-8", "replace")
                if not assert_that(url in pub, "new photo appears on room page"):
                    failures += 1

            print("\n--- admin rejects a bad upload cleanly ---")
            bad_token = csrf_of(client, imgs_url)
            bad = client.post(
                imgs_url,
                data={"_csrf_token": bad_token, "image": (io.BytesIO(b"not an image at all"), "evil.exe")},
                content_type="multipart/form-data",
                follow_redirects=True,
            )
            bad_body = bad.data.decode("utf-8", "replace")
            if not assert_that(bad.status_code < 400, "bad upload does not 500", str(bad.status_code)):
                failures += 1
            if not assert_that(
                "پسوند فایل مجاز نیست" in bad_body,
                "bad upload shows a Persian error",
            ):
                failures += 1
            with app.app_context():
                after_bad = query_one(
                    "SELECT count(*) c FROM scenario_images WHERE scenario_id=?", (sid,)
                )["c"]
            if not assert_that(after_bad == after, "rejected upload stored nothing", f"{after_bad} rows"):
                failures += 1

        print("\n--- admin: content editor ---")
        if not check(client, "post", "/admin/content", "save home content",
                     data={"_csrf_token": admin_token, "tab": "home",
                           "home.hero_cta": "مشاهده اتاق‌ها", "home.hero_text": "متن آزمایشی"},
                     follow_redirects=True):
            failures += 1
        with app.app_context():
            from app.services.settings import get_setting

            if get_setting("home.hero_cta") != "مشاهده اتاق‌ها":
                print("FAIL  home content was not saved")
                failures += 1
            else:
                print("ok    home content persisted")

        print("\n--- admin: schedule CRUD ---")
        with app.app_context():
            scenario_id = query_one("SELECT id FROM scenarios WHERE status='published' LIMIT 1")["id"]
        sched = {
            "_csrf_token": admin_token,
            "scenario_id": str(scenario_id),
            "weekday": "0",
            "open_time": "10:00",
            "close_time": "23:00",
            "slot_minutes": "90",
            "capacity": "8",
            "price_override": "",
            "valid_from": "",
            "valid_to": "",
            "is_active": "1",
        }
        if not check(client, "post", "/admin/schedules", "create schedule",
                     data=sched, follow_redirects=True):
            failures += 1
        with app.app_context():
            new_sched = query_one(
                "SELECT * FROM schedules WHERE scenario_id=? AND slot_minutes=90", (scenario_id,)
            )
        if new_sched is None:
            print("FAIL  schedule was not persisted")
            failures += 1
        else:
            if not check(client, "post", f"/admin/schedules/{new_sched['id']}/toggle",
                         "toggle schedule", data={"_csrf_token": admin_token},
                         follow_redirects=True):
                failures += 1

        print("\n--- public booking flow (real reservation) ---")
        # A guest cannot book any more: create a real customer account, sign in
        # with it, and run the whole flow as that customer.
        with app.app_context():
            execute(
                "INSERT INTO customers(name, phone, password_hash, must_change_pw) "
                "VALUES (?,?,?,0) ON CONFLICT(phone) DO UPDATE SET "
                "password_hash=excluded.password_hash, must_change_pw=0",
                ("رضا نمونه", BOOKER_PHONE, hash_password(BOOKER_PASSWORD)),
            )
            booker_id = query_one("SELECT id FROM customers WHERE phone=?", (BOOKER_PHONE,))["id"]
        login_token = csrf_of(client, "/login")
        # Signing in from the booking redirect must land back on the booking
        # page (that is what ?next= carries through the login form).
        login_resp = client.post(
            f"/login?next=/book/{slug}",
            data={"_csrf_token": login_token, "identifier": BOOKER_PHONE,
                  "password": BOOKER_PASSWORD},
            follow_redirects=True,
        )
        if not assert_that(
            login_resp.status_code == 200
            and 'class="booking-form"' in login_resp.get_data(as_text=True),
            "login lands back on the booking page",
            f"{login_resp.status_code} {login_resp.request.path}",
        ):
            failures += 1
        booking_html = client.get(f"/book/{slug}").data.decode("utf-8", "replace")
        if not assert_that(
            BOOKER_PHONE in booking_html and "رزرو برای حساب" in booking_html,
            "booking page prefilled from account", BOOKER_PHONE,
        ):
            failures += 1
        with app.app_context():
            from app.services.availability import business_today

            from app.services.availability import business_now, slot_start

            target = business_today()
            for _ in range(30):
                slots = _rows(
                    "SELECT id, slot_date, start_time FROM slots WHERE scenario_id=? AND slot_date=? AND status='open' "
                    "ORDER BY start_time LIMIT 1",
                    (scenario_id, target.isoformat()),
                )
                if slots:
                    break
                target = target.replace(day=target.day)
                import datetime as _dt

                target = _dt.date.fromordinal(target.toordinal() + 1)
            # A session that already started is refused by the server ("این
            # ساعت گذشته است"), so book the first slot that has not started
            # yet — today's own first up-coming slot, else walk the next days
            # forward. Without this the smoke suite is green in the morning
            # and red in the afternoon.
            future_days = _rows(
                "SELECT id, slot_date, start_time FROM slots WHERE scenario_id=? "
                "AND slot_date>? AND status='open' ORDER BY slot_date, start_time LIMIT 5",
                (scenario_id, business_today().isoformat()),
            )
            slot_row = None
            for candidate in slots + future_days:
                starts = slot_start(candidate)
                if starts is not None and starts > business_now():
                    slot_row = candidate
                    break
            if slot_row is None:
                print("FAIL  no bookable slot remains: the day ran out of future sessions")
                raise SystemExit(1)
            slot_id = slot_row["id"]
        book_token = csrf_of(client, f"/book/{slug}")
        if not check(client, "post", f"/book/{slug}", "submit booking",
                     data={"_csrf_token": book_token, "slot_id": str(slot_id),
                           "participants": "2", "first_name": "رضا",
                           "family_name": "نمونه", "phone": BOOKER_PHONE,
                           "email": "test@example.com", "note": "یادداشت تست"},
                     follow_redirects=True):
            failures += 1
        with app.app_context():
            reservation = query_one(
                "SELECT r.* FROM reservations r WHERE r.customer_id=?", (booker_id,)
            )
        if reservation is None:
            print("FAIL  reservation was not persisted")
            failures += 1
        else:
            code = reservation["code"]
            print(f"ok    reservation {code} @ {reservation['total_price']:,}")
            if not check(client, "get", f"/booking/{code}", "confirmation page"):
                failures += 1
            if not check(client, "get", f"/review/{code}", "review eligibility page"):
                failures += 1
            # Signing in as a customer ends the administrator session in this
            # browser (one identity per session), so the admin must sign in
            # again before the admin-side assertions that follow.
            if not check(client, "get", "/admin/",
                         "admin signed out by customer login", expect=302):
                failures += 1
            admin_token = csrf_of(client, "/admin/login")
            if not check(client, "post", "/admin/login", "admin sign-in again",
                         data={"username": "admin", "password": "Admin12345678",
                               "_csrf_token": admin_token},
                         follow_redirects=True):
                failures += 1
            if not check(client, "get", f"/admin/bookings/{reservation['id']}",
                         "admin booking detail"):
                failures += 1

            print("\n--- admin: status transition ---")
            detail_token = csrf_of(client, f"/admin/bookings/{reservation['id']}")
            if not check(client, "post", f"/admin/bookings/{reservation['id']}/status",
                         "confirm reservation",
                         data={"_csrf_token": detail_token, "status": "confirmed",
                               "note": "تأیید آزمایشی"},
                         follow_redirects=True):
                failures += 1
            if not check(client, "post", f"/admin/bookings/{reservation['id']}/status",
                         "complete reservation",
                         data={"_csrf_token": detail_token, "status": "completed"},
                         follow_redirects=True):
                failures += 1
            with app.app_context():
                after = query_one("SELECT status, completed_at FROM reservations WHERE id=?",
                                  (reservation["id"],))
                history = _rows("SELECT * FROM reservation_history WHERE reservation_id=?",
                                (reservation["id"],))
                notif = _rows("SELECT * FROM notifications WHERE reservation_id=?",
                              (reservation["id"],))
            if after and after["status"] == "completed" and after["completed_at"]:
                print(f"ok    reservation completed, {len(history)} history rows")
            else:
                print(f"FAIL  reservation status is {after and after['status']!r}")
                failures += 1
            if notif:
                print(f"ok    {len(notif)} notification(s) recorded")
            else:
                print("FAIL  no notification recorded for the booking")
                failures += 1

            print("\n--- review flow ---")
            review_token = csrf_of(client, f"/review/{code}")
            if not check(client, "post", f"/review/{code}", "submit review",
                         data={"_csrf_token": review_token, "rating": "5",
                               "title": "عالی بود", "body": "تجربه بسیار خوبی بود، پیشنهاد می‌کنم."},
                         follow_redirects=True):
                failures += 1
            with app.app_context():
                pending = query_one(
                    "SELECT * FROM reviews WHERE reservation_id=?", (reservation["id"],)
                )
            if pending is None:
                print("FAIL  review was not persisted")
                failures += 1
            else:
                print(f"ok    review stored with status={pending['status']}")
                # Fresh token: the session (and therefore its CSRF secret) has
                # changed since the admin signed in again above.
                moderate_token = csrf_of(client, f"/admin/reviews/{pending['id']}")
                if not check(client, "post", f"/admin/reviews/{pending['id']}/moderate",
                             "approve review",
                             data={"_csrf_token": moderate_token, "action": "approve"},
                             follow_redirects=True):
                    failures += 1
                if not check(client, "get", "/api/reviews/stats", "review stats after approval"):
                    failures += 1

        print("\n--- admin: customers & reports ---")
        if not check(client, "get", "/admin/customers", "customers list"):
            failures += 1
        if not check(client, "get", "/admin/reports/export/reservations?days=30", "csv export"):
            failures += 1
        if not check(client, "get", "/admin/reports/export/customers", "customers csv export"):
            failures += 1
        if not check(client, "get", "/admin/admins", "admin account list"):
            failures += 1

        print("\n--- reviews section tracks real content (reference layout) ---")
        # By now the review flow above has approved one review, so the block
        # must be present. The empty-state case is asserted earlier, before any
        # review exists; both directions are covered.
        home_now = client.get("/").data.decode("utf-8", "replace")
        if "reviews-section" in home_now:
            print("ok    reviews block shown once a review exists")
        else:
            print("FAIL  reviews block missing even though an approved review exists")
            failures += 1
        for asset in ("/static/img/tear.png", "/static/img/favicon.png",
                      "/static/img/apple-touch-icon.png"):
            if not check(client, "get", asset, asset.split("/")[-1]):
                failures += 1

        print("\n--- 404 handling ---")
        if not check(client, "get", "/rooms/no-such-room", "unknown slug", expect=404):
            failures += 1
        if not check(client, "get", "/admin/rooms/999999/edit", "unknown admin room", expect=404):
            failures += 1
        if not check(client, "get", "/admin/bookings/999999", "unknown admin booking", expect=404):
            failures += 1

        print("\n--- permission enforcement (secondary admin) ---")
        client.get("/admin/logout", follow_redirects=True)
        token = csrf_of(client, "/admin/login")
        if not check(client, "post", "/admin/login", "login as staff",
                     data={"username": "staff", "password": "Staff12345678",
                           "_csrf_token": token},
                     follow_redirects=True):
            failures += 1
        if not check(client, "get", "/admin/logs", "staff blocked from audit log", expect=403):
            failures += 1
        if not check(client, "get", "/admin/admins", "staff blocked from admin list", expect=403):
            failures += 1
        if not check(client, "get", "/admin/rooms", "staff without scenarios.manage", expect=403):
            failures += 1

    print()
    if failures:
        print(f"SMOKE FAILED — {failures} check(s) did not pass.")
        return 1
    print("SMOKE PASSED — every route rendered and every write flow persisted.")
    return 0


def _rows(sql, args=()):
    from app.db import query

    return query(sql, args)


if __name__ == "__main__":
    raise SystemExit(main())
