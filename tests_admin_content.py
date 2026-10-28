"""Admin content-editor coverage: the polaroid gallery, image picker and the
explicit "clear this field" action must all work from the real admin forms and
be reflected on the public pages.

Run: PYTHONIOENCODING=utf-8 python tests_admin_content.py
"""
from __future__ import annotations

import json
import sys

sys.path.insert(0, ".")

from app import create_app
from app.db import execute, query_one
from app.services.settings import (
    DEFAULT_POLAROIDS,
    get_polaroids,
    get_setting,
    set_setting,
)

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))


app = create_app()
client = app.test_client()
_ctx = app.app_context()
_ctx.push()

TOK = "content-coverage-token"
with client.session_transaction() as sess:
    sess["admin_id"] = 1
    sess["_csrf_token"] = TOK


def html(path: str) -> str:
    r = client.get(path)
    assert r.status_code == 200, f"{path} -> {r.status_code}"
    return r.get_data(as_text=True)


# Snapshot every key this suite touches — raw row value or None when the row
# does not exist — so the developer's database is left exactly as found.
TOUCHED = (
    "home.polaroids",
    "home.hero_image",
    "pages.about",
    "pages.rules",
    "site.tagline",
)


def _snapshot(key: str) -> str | None:
    row = query_one("SELECT value FROM settings WHERE key=?", (key,))
    return None if row is None else row["value"]


ORIGINALS_RAW = {key: _snapshot(key) for key in TOUCHED}


def _decoded(key: str):
    raw = ORIGINALS_RAW[key]
    if raw is None:
        return get_setting(key)
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return raw


ORIGINALS = {key: _decoded(key) for key in TOUCHED}
ORIGINAL_POLAROIDS = ORIGINALS["home.polaroids"]
ORIGINAL_ABOUT = ORIGINALS["pages.about"]
ORIGINAL_TAGLINE = ORIGINALS["site.tagline"]


def restore_settings() -> None:
    for key, raw in ORIGINALS_RAW.items():
        if raw is None:
            execute("DELETE FROM settings WHERE key=?", (key,))
        else:
            execute("UPDATE settings SET value=? WHERE key=?", (raw, key))

# ------------------------------------------------------------ defaults show
page = html("/admin/content/polaroids")
check("polaroids tab renders", "polaroidRows" in page)
check("default rows are listed", all(s["src"] in page for s in DEFAULT_POLAROIDS), "")

home = html("/")
check("homepage renders default strip", DEFAULT_POLAROIDS[0]["src"] in home)

# ------------------------------------------------------------- save two rows
new_rows = [
    {"src": "/static/uploads/media/first.jpg", "caption": "ردیف اول", "alt": "اول"},
    {"src": "/static/uploads/media/second.jpg", "caption": "ردیف دوم", "alt": "دوم"},
]
r = client.post(
    "/admin/content/polaroids",
    data={
        "_csrf_token": TOK,
        "polaroid_src": [s["src"] for s in new_rows],
        "polaroid_caption": [s["caption"] for s in new_rows],
        "polaroid_alt": [s["alt"] for s in new_rows],
    },
    follow_redirects=False,
)
check("polaroid save redirects", r.status_code == 302, f"status={r.status_code}")
check("polaroids persisted", get_polaroids() == new_rows, repr(get_polaroids()))

home = html("/")
check("new strip on homepage", all(s["src"] in home for s in new_rows))
check("removed shot gone from homepage", DEFAULT_POLAROIDS[0]["src"] not in home)
check("caption rendered", "ردیف اول" in home)

# ------------------------------------------------- add / remove in one POST
mixed = [
    {"src": "/static/uploads/media/keep.jpg", "caption": "بماند", "alt": ""},
    {"src": "", "caption": "حذف شدنی", "alt": ""},
]
r = client.post(
    "/admin/content/polaroids",
    data={
        "_csrf_token": TOK,
        "polaroid_src": [s["src"] for s in mixed],
        "polaroid_caption": [s["caption"] for s in mixed],
        "polaroid_alt": [s["alt"] for s in mixed],
    },
    follow_redirects=False,
)
saved = get_polaroids()
check("blank src row dropped", len(saved) == 1 and saved[0]["src"].endswith("keep.jpg"),
      repr(saved))
check("alt falls back to caption", saved and saved[0]["alt"] == "بماند", repr(saved))

# --------------------------------------------------------- all empty resets
r = client.post(
    "/admin/content/polaroids",
    data={"_csrf_token": TOK, "polaroid_src": ["", ""], "polaroid_caption": ["", ""]},
    follow_redirects=False,
)
check("emptying every row restores defaults",
      get_polaroids() == [dict(i) for i in DEFAULT_POLAROIDS], repr(get_polaroids()))

# ----------------------------------------------------- bad src never stored
r = client.post(
    "/admin/content/polaroids",
    data={"_csrf_token": TOK, "polaroid_src": ["javascript:alert(1)"]},
    follow_redirects=False,
)
check("javascript: src ignored", all(not s["src"].startswith("javascript") for s in get_polaroids()),
      repr(get_polaroids()))

# --------------------------------------------------------- image picker UX
set_setting("home.hero_image", "/static/uploads/media/hero.jpg")
page = html("/admin/content/home")
check("image field shows a picker", "data-pick-media" in page)
check("image preview rendered", "/static/uploads/media/hero.jpg" in page)
check("media modal present", "mediaModal" in page)

# ------------------------------------------------------- clear a text value
r = client.post(
    "/admin/content",
    data={"tab": "pages", "_csrf_token": TOK, "pages.about": "متن تازه", "pages.rules": "x"},
    follow_redirects=False,
)
check("pages save redirects", r.status_code == 302, f"status={r.status_code}")
check("about saved", get_setting("pages.about") == "متن تازه", repr(get_setting("pages.about")))

# Blank without the flag must be ignored (an accident must not wipe a page)...
client.post("/admin/content", data={"tab": "pages", "_csrf_token": TOK, "pages.about": ""},
            follow_redirects=False)
check("blank textarea alone does not wipe", get_setting("pages.about") == "متن تازه",
      repr(get_setting("pages.about")))

# ...but the explicit clear flag empties it.
client.post("/admin/content", data={"tab": "pages", "_csrf_token": TOK, "pages.about": "",
                                    "__clear": "pages.about"}, follow_redirects=False)
check("explicit clear empties the field", get_setting("pages.about") == "",
      repr(get_setting("pages.about")))

# ------------------------------------------------------ permissions UI bits
admins_page = html("/admin/admins")
check("select-all button present", 'data-check-all="1"' in admins_page)
check("clear-all button present", 'data-check-all="0"' in admins_page)
check("delete admin button present", "/delete" in admins_page)

# ----------------------------------------------------------------- restore
restore_settings()
restored = {key: get_setting(key) for key in TOUCHED}
check("originals restored",
      all(restored[key] == ORIGINALS[key] for key in TOUCHED),
      repr({k: (restored[k], ORIGINALS[k]) for k in TOUCHED
            if restored[k] != ORIGINALS[k]}))

failed = [x for x in RESULTS if not x[1]]
for name, ok, detail in RESULTS:
    print(("PASS  " if ok else "FAIL  ") + name + (("  <- " + detail) if (not ok and detail) else ""))
print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
_ctx.pop()
sys.exit(1 if failed else 0)
