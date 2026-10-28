"""End-to-end admin coverage test: edits saved through the admin forms must
actually appear on the public site. Run: PYTHONIOENCODING=utf-8 python tests_admin_coverage.py
"""
from __future__ import annotations

import re
import sys

sys.path.insert(0, ".")

from app import create_app
from app.services.settings import get_setting, set_many

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))


def html_of(client, path: str) -> str:
    r = client.get(path)
    assert r.status_code == 200, f"{path} -> {r.status_code}"
    return r.get_data(as_text=True)


app = create_app()
client = app.test_client()
_ctx = app.app_context()
_ctx.push()

# ---------------------------------------------------------------- originals
ORIGINAL = {k: get_setting(k) for k in (
    "site.instagram", "footer.copyright", "seo.title_suffix", "seo.robots_index",
    "brand.logo_text", "site.tagline", "site.address", "site.email",
    "site.working_hours", "site.phone", "nav.links",
)}

# log in as the super admin by seeding the session directly (no password needed)
with client.session_transaction() as sess:
    sess["admin_id"] = 1
    sess["_csrf_token"] = "coverage-test-token"
tok = "coverage-test-token"
check("admin session seeded", True, "")

# ------------------------------------------------- save through the real form
TEST_INSTAGRAM = "https://instagram.com/admin-coverage-test"
r = client.post("/admin/content", data={
    "tab": "site", "_csrf_token": tok,
    "site.instagram": TEST_INSTAGRAM,
    "site.tagline": "شعار آزمایشی ادمین",
    "site.address": "تهران، خیابان تست ۱",
    "site.email": "test@example.com",
    "site.working_hours": "شنبه تا چهارشنبه ۱۰ تا ۲۰",
    "site.phone": "۰۲۱-۹۹۹۹۹۹۹۹",
    "footer.copyright": "کپی‌رایت آزمایشی ۱۴۰۵",
}, follow_redirects=False)
check("site tab saves (302)", r.status_code == 302, f"status={r.status_code}")
check("instagram persisted", get_setting("site.instagram") == TEST_INSTAGRAM,
      repr(get_setting("site.instagram")))
check("footer.copyright persisted", get_setting("footer.copyright") == "کپی‌رایت آزمایشی ۱۴۰۵",
      repr(get_setting("footer.copyright")))

# Unchecking a box must persist. The form always posts the hidden "0" twin
# before the checkbox, so an unchecked box arrives as "seo.robots_index=0" —
# exactly what this POST reproduces.
r = client.post("/admin/content", data={"tab": "seo", "_csrf_token": tok,
                                        "seo.robots_index": "0",
                                        "seo.title_suffix": " | سئوی آزمایشی"},
                follow_redirects=False)
check("robots_index OFF saves when unchecked", get_setting("seo.robots_index") is False,
      repr(get_setting("seo.robots_index")))
check("title_suffix saves", get_setting("seo.title_suffix").strip() == "| سئوی آزمایشی",
      repr(get_setting("seo.title_suffix")))

# ...while a POST that never mentions the key at all (a partial save of the
# tab) must leave the toggle untouched instead of switching it off silently.
client.post("/admin/content", data={"tab": "seo", "_csrf_token": tok,
                                    "seo.robots_index": "1"}, follow_redirects=False)
client.post("/admin/content", data={"tab": "seo", "_csrf_token": tok,
                                    "seo.title_suffix": " | سئوی آزمایشی"},
            follow_redirects=False)
check("partial POST leaves the toggle alone", get_setting("seo.robots_index") is True,
      repr(get_setting("seo.robots_index")))
# Back OFF for the page-rendering assertions below (they expect noindex).
client.post("/admin/content", data={"tab": "seo", "_csrf_token": tok,
                                    "seo.robots_index": "0"}, follow_redirects=False)
check("robots_index OFF again for the page checks",
      get_setting("seo.robots_index") is False, repr(get_setting("seo.robots_index")))

# nav JSON valid + invalid
r = client.post("/admin/content", data={
    "tab": "nav", "_csrf_token": tok,
    "nav.links": '[{"href": "/", "label": "خانه"}, {"href": "/rooms", "label": "اتاق‌ها"}, '
                 '{"href": "javascript:alert(1)", "label": "بد"}, '
                 '{"href": "/contact", "label": "تماس"}, {"href": "https://x.com", "label": "ایکس"}]',
}, follow_redirects=False)
saved_nav = get_setting("nav.links")
check("nav tab saves", r.status_code == 302, f"status={r.status_code}")

r = client.post("/admin/content", data={"tab": "nav", "_csrf_token": tok,
                                        "nav.links": "{not json"}, follow_redirects=False)
check("invalid nav JSON rejected", get_setting("nav.links") == saved_nav,
      repr(get_setting("nav.links"))[:60])

# brand logo
r = client.post("/admin/content", data={"tab": "site", "_csrf_token": tok,
                                        "brand.logo_text": "AZAD\nROOM"}, follow_redirects=False)
check("brand.logo_text saves", get_setting("brand.logo_text") == "AZAD\nROOM",
      repr(get_setting("brand.logo_text")))

# ------------------------------------------------- verify on public pages
home = html_of(client, "/")
check("footer shows edited instagram", TEST_INSTAGRAM in home)
check("footer shows edited copyright", "کپی‌رایت آزمایشی ۱۴۰۵" in home)
check("footer shows tagline", "شعار آزمایشی ادمین" in home)
check("brand shows edited logo", "AZAD" in home and "ROOM" in home)
check("title shows edited suffix", " | سئوی آزمایشی" in home)
check("robots noindex when OFF", 'content="noindex, nofollow"' in home)
check("nav renders CMS items + drops javascript: href", "javascript:alert" not in home and "تماس" in home)
check("nav keeps https item", "https://x.com" in home)
check("no google fonts", "googleapis" not in home)

contact = html_of(client, "/contact")
check("contact shows edited address", "تهران، خیابان تست ۱" in contact)
# The email row was removed from the contact page by request: the setting may
# still be edited in the panel, but it must not be rendered here.
check(
    "contact hides the removed email row",
    "test@example.com" not in contact and "mailto:" not in contact and "ایمیل" not in contact,
)
check("contact shows credit line", "created by Matin Alinghian" in contact)
check("contact shows edited hours", "شنبه تا چهارشنبه ۱۰ تا ۲۰" in contact)
check("contact shows edited phone", "۰۲۱-۹۹۹۹۹۹۹۹" in contact)

rooms = html_of(client, "/rooms")
check("rooms page renders", len(rooms) > 1000)
detail = html_of(client, "/rooms/atakh-bimaristan")
check("room detail renders + title suffix", " | سئوی آزمایشی" in detail)

# robots index ON again
tok = "coverage-test-token"
client.post("/admin/content", data={"tab": "seo", "_csrf_token": tok,
                                    "seo.robots_index": "1", "seo.title_suffix": ORIGINAL["seo.title_suffix"]})
check("robots_index ON restores", get_setting("seo.robots_index") is True)

# --------------------------------------------------------------- restore
set_many(ORIGINAL)
check("originals restored", all(get_setting(k) == v for k, v in ORIGINAL.items()))

# ------------------------------------------------------------------ report
failed = [x for x in RESULTS if not x[1]]
for name, ok, detail in RESULTS:
    print(("PASS  " if ok else "FAIL  ") + name + (("  <- " + detail) if (not ok and detail) else ""))
print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
_ctx.pop()
sys.exit(1 if failed else 0)
