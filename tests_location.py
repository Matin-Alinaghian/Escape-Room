"""Regression tests for the Neshan location feature.

Run:  PYTHONIOENCODING=utf-8 python tests_location.py

Covers:
  * neshan_search_url helper builds correct URLs (and handles empty input)
  * the ``map_url`` Jinja filter is registered and routes through the helper
  * admin-editable ``site.map_url`` setting overrides the derived link
  * footer, contact page and room-detail page render the icon + Neshan link
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))


from app import create_app
from app.services.neshan import neshan_search_url

app = create_app()

# 1. Helper -----------------------------------------------------------------
u = neshan_search_url("تهران، خیابان آزادی، پلاک ۱۲")
check("helper builds neshan search URL", u.startswith("https://neshan.org/maps/search/"), u)
check("helper escapes empty input", neshan_search_url("") == "")
check("helper strips whitespace", neshan_search_url("   تهران   ") == neshan_search_url("تهران"))

# 2. Filter registration ----------------------------------------------------
with app.app_context():
    from app.services.settings import get_setting, set_setting

    check("map_url filter registered", "map_url" in app.jinja_env.filters)
    # The filter's contract is "admin pin wins, else derive from the address";
    # the pin is therefore cleared while the derived path is asserted.
    pin = get_setting("site.map_url", "")
    set_setting("site.map_url", "")
    try:
        check(
            "filter routes through helper",
            app.jinja_env.filters["map_url"]("اصفهان").startswith("https://neshan.org/maps/search/"),
        )
    finally:
        set_setting("site.map_url", pin)

# 3. Admin override via site.map_url ----------------------------------------
with app.app_context():
    from app.services.settings import set_setting, get_setting, DEFAULTS

    original = get_setting("site.map_url", "")
    set_setting("site.map_url", "https://neshan.org/maps/@35.7,51.4,15z")
    try:
        with app.test_client() as c:
            html = c.get("/contact").get_data(as_text=True)
            check("admin-provided map link wins",
                  "neshan.org/maps/@35.7" in html)
    finally:
        set_setting("site.map_url", original if original is not None else "")
    check("setting restored", get_setting("site.map_url", "") == (original if original is not None else ""))

# 4. Page rendering ----------------------------------------------------------
# The seeded default pins an exact short link in ``site.map_url`` (that pin is
# what section 3 above covers). Clear it here so the assertions below are about
# the *derived* Neshan search link the pages must build from the address text.
with app.app_context():
    from app.services.settings import get_setting, set_setting

    pinned_map_url = get_setting("site.map_url", "")
    set_setting("site.map_url", "")
    with app.test_client() as c:
        html = c.get("/contact")
        check("contact page 200", html.status_code == 200, str(html.status_code))
        body = html.get_data(as_text=True)
        check("contact has neshan link", "neshan.org/maps/search" in body)
        check("contact address row has pin icon", body.count("<svg") >= 1)

        home = c.get("/")
        check("home 200", home.status_code == 200)
        h = home.get_data(as_text=True)
        check("footer has location block", "footer-location" in h)
        check("footer has neshan link", "neshan.org/maps/search" in h)

        rooms = c.get("/rooms")
        check("rooms 200", rooms.status_code == 200)

        from app.db import query_one

        sc = query_one("SELECT slug FROM scenarios WHERE address != '' LIMIT 1")
        if sc:
            detail = c.get(f"/rooms/{sc['slug']}")
            check("room detail 200", detail.status_code == 200, str(detail.status_code))
            d = detail.get_data(as_text=True)
            check("room detail has neshan link", "neshan.org/maps/search" in d)
            check("room detail has pin icon", "M12 2a7 7 0 0 0-7 7" in d)
        else:
            check("room detail skipped (no scenario with address)", True)
    set_setting("site.map_url", pinned_map_url)
    check("map link restored", get_setting("site.map_url", "") == pinned_map_url,
          repr(get_setting("site.map_url", "")))

# ------------------------------------------------------------------ report
failed = [x for x in RESULTS if not x[1]]
for name, ok, detail in RESULTS:
    print(("PASS  " if ok else "FAIL  ") + name + (("  <- " + detail) if (not ok and detail) else ""))
print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
sys.exit(1 if failed else 0)
