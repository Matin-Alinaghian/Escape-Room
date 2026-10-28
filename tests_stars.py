"""Regression tests for the proportional star meter (average ratings).

Run:  PYTHONIOENCODING=utf-8 python tests_stars.py

Background: the average used to be rendered with ``average|round|int``, so a
room rated 4.75 out of 5 looked like a perfect five-star room. The ``stars``
filter now clips the lit star row to the exact percentage, so the last star is
only as full as the average says it is.

Covers:
  * filter maths: 4.75 -> 95%, clamping, junk input, readable aria text
  * whole-star markup is kept for one review's rating (always an integer)
  * every public page showing an average renders the exact percentage the
    rating service reports, and never a full 5 stars for a sub-5 average
  * no template rounds an average into whole stars any more
"""
from __future__ import annotations

import pathlib
import re
import sys

sys.path.insert(0, ".")

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))


from app import create_app
from app.db import query
from app.services import reviews

app = create_app()
stars = app.jinja_env.filters["stars"]

PCT_RE = re.compile(r'class="stars star-meter"[^>]*style="--pct:([0-9.]+)%"')

# ==========================================================================
# 1. The filter itself
# ==========================================================================
with app.app_context():
    html = str(stars(4.75))
    check("4.75 fills 95% of the row", 'style="--pct:95.0000%"' in html, html)
    check("the meter has a dim track and a lit copy",
          html.count("★" * 5) == 2 and 'class="star-track"' in html and 'class="star-fill"' in html,
          html)
    check("aria label carries the exact value",
          'aria-label="امتیاز ۴.۷۵ از ۵"' in html, html)
    check("the meter exposes itself as an image to screen readers",
          'role="img"' in html, html)

    check("4.30 fills 86%", 'style="--pct:86.0000%"' in str(stars(4.3)), str(stars(4.3)))
    check("a perfect 5 fills the whole row", 'style="--pct:100.0000%"' in str(stars(5)))
    check("4.00 leaves one star empty", 'style="--pct:80.0000%"' in str(stars(4.0)))

    check("0 fills nothing", 'style="--pct:0.0000%"' in str(stars(0)))
    check("None fills nothing", 'style="--pct:0.0000%"' in str(stars(None)))
    check("unparsable input fills nothing", 'style="--pct:0.0000%"' in str(stars("به‌زودی")))
    check("out-of-range low clamps to 0", 'style="--pct:0.0000%"' in str(stars(-3)))
    check("out-of-range high clamps to 100", 'style="--pct:100.0000%"' in str(stars(9)))

    check("trailing zeros are dropped from the label",
          'aria-label="امتیاز ۴ از ۵"' in str(stars(4.0)) and '۴.۰۰' not in str(stars(4.0)))
    check("a custom label is honoured",
          'aria-label="کیفیت اتاق ۳.۲ از ۵"' in str(stars(3.2, "کیفیت اتاق")))
    check("label text is escaped", "&lt;" in str(stars(3, "<b>")), str(stars(3, "<b>")))

    # The meter markup is emitted as safe HTML, not as escaped text.
    check("filter output is not double-escaped",
          "&lt;span" not in str(stars(4.75)) and "<span" in str(stars(4.75)))

# ==========================================================================
# 2. Whole stars survive for a single review (integer ratings)
# ==========================================================================
with app.app_context():
    room = query("SELECT slug FROM scenarios WHERE status='published' ORDER BY id LIMIT 1")[0]
    page = app.test_client().get(f"/rooms/{room['slug']}")
    check("room detail 200", page.status_code == 200, str(page.status_code))
    body = page.get_data(as_text=True)

    review_stars = re.search(r'<div class="review-stars"[^>]*>(.*?)</div>', body, re.S)
    check("a review's own rating still uses whole stars",
          review_stars is not None
          and review_stars.group(1).count("★") == 5
          and review_stars.group(1).count('class="on"') == 5
          and "--pct" not in review_stars.group(1),
          (review_stars.group(1)[:120] if review_stars else "no review-stars block"))
    # Two averages are on this page: the hero chip and the opinions block.
    check("both averages on the room page use the meter", len(PCT_RE.findall(body)) == 2,
          str(PCT_RE.findall(body)))

# ==========================================================================
# 3. Live pages: the percentage must match the service, never round up to 5
# ==========================================================================
with app.app_context():
    stats = {
        s["id"]: reviews.stats(s["id"])
        for s in query("SELECT id FROM scenarios WHERE status='published' ORDER BY id")
    }
    room_slugs = [s["slug"] for s in query("SELECT slug FROM scenarios WHERE status='published' ORDER BY id")]
    overall = reviews.stats()

expected_pcts = {f"{st['average'] / 5 * 100:.4f}" for st in stats.values() if st["has_reviews"]}
# Every room is reviewed by its own customers, so no two rooms may end up with
# the same average (one room at 4.5 next to one at 5 is the normal case), and at
# least one of them must be fractional — that is the meter case under test.
averages = [st["average"] for st in stats.values() if st["has_reviews"]]
check("each room carries its own average (never one number repeated)",
      len(averages) > 1 and len(set(averages)) == len(averages),
      str(sorted(averages)))
check("the fixture still covers a fractional average (the case being tested)",
      any(a != int(a) for a in averages),
      str(sorted(averages)))
averages = set(averages)

client = app.test_client()

with app.app_context():
    from app.jalali import fa_digits

    pages = {
        "home": client.get("/").get_data(as_text=True),
        "rooms": client.get("/rooms").get_data(as_text=True),
        "reviews": client.get("/reviews").get_data(as_text=True),
    }
    for slug in room_slugs:
        pages[f"room:{slug}"] = client.get(f"/rooms/{slug}").get_data(as_text=True)

    for name, body in pages.items():
        found = set(PCT_RE.findall(body))
        check(f"{name}: renders a star meter", bool(found), str(sorted(found)))
        check(f"{name}: no percentage invented",
              found.issubset(expected_pcts | {f"{overall['average'] / 5 * 100:.4f}"}),
              f"{sorted(found)} vs {sorted(expected_pcts)}")
        check(f"{name}: a 4.75 average never shows five full stars",
              "100.0000" not in found or any(a == 5 for a in averages),
              str(sorted(found)))

    home = pages["home"]
    check("home: the 4.75 average is drawn at 95%", "--pct:95.0000%" in home)
    check("home: the average is printed in Persian digits", "۴.۷۵" in home)
    check("home: the review count is printed in Persian digits", "۴ نظر" in home)
    check("reviews page: the count under the big average is Persian",
          re.search(r"rating-count\">از [۰-۹]+ نظر", pages["reviews"]) is not None)

    # The room card rating used to be hidden from screen readers entirely; the
    # meter now describes itself, so the average must be announced once.
    card = re.search(r'style="--pct:95\.0000%"[^>]*aria-label="میانگین امتیاز کاربران ۴\.۷۵ از ۵"', home)
    check("home: room cards describe the average for screen readers", card is not None)

# ==========================================================================
# 4. Nothing may go back to rounding the average into whole stars
# ==========================================================================
bad: list[str] = []
for path in sorted(pathlib.Path("app/templates").rglob("*.html")):
    text = path.read_text(encoding="utf-8")
    if "average|round" in text or "average |round" in text:
        bad.append(str(path))
check("no template rounds an average into stars", not bad, ", ".join(bad))

css = pathlib.Path("app/static/css/base.css").read_text(encoding="utf-8")
check("the meter CSS clips the lit layer to --pct",
      ".stars.star-meter .star-fill" in css and "width:var(--pct" in css)

# The lit layer grows into place instead of snapping to its final width, and
# the growth is skipped entirely for anyone who asked for less motion.
check("the lit layer animates from zero to its --pct",
      "@keyframes star-meter-grow" in css
      and "animation:star-meter-grow" in css
      and "from{width:0}" in css)
check("the meter animation is disabled under reduced motion",
      "@media (prefers-reduced-motion:reduce)" in css and "animation:none" in css)

# No scroll gutter anywhere: the page and every inner scroller keep working,
# but neither the vertical nor the horizontal bar is ever painted.
check("no scrollbar is painted in any direction",
      "scrollbar-width:none" in css
      and ":-webkit-scrollbar" in css
      and "display:none" in css)

js = pathlib.Path("app/static/js/base.js").read_text(encoding="utf-8")
check("off-screen meters wait at zero until they scroll in",
      "meter-hold" in css and "meter-hold" in js and "IntersectionObserver" in js)
check("the meter animation honours reduced-motion in JS too",
      "prefers-reduced-motion: reduce" in js)

# A held lit layer is parked at width:0, and an observer never reports a
# zero-area target — watching it left every rating below the fold invisible.
# The meter (sized by its dim track) is observed instead, and a plain scroll
# sweep releases anything a silent observer missed.
check("the scroll-in watcher observes the meter, never the collapsed lit layer",
      "io.observe(meter)" in js and "observe(fill" not in js)
check("a scroll/resize sweep releases meters the observer never reported",
      "function sweep()" in js
      and "addEventListener('scroll', onScroll" in js
      and "addEventListener('resize', onScroll" in js
      and "addEventListener('visibilitychange', onVisible)" in js)
check("a released meter is unobserved and the listeners go with it",
      "io.unobserve(meter)" in js and "function teardown()" in js)
# The observer and the scroll listener both ride on the browser painting frames;
# an occluded or throttled tab delivers neither, and a rating parked at width 0
# must never be what the visitor is left looking at.
check("a timer sweeps the held meters even when no frame is painted",
      "watchdog = setInterval(sweep, 400)" in js
      and "clearInterval(watchdog)" in js)

# ------------------------------------------------------------------ report
failed = [x for x in RESULTS if not x[1]]
for name, ok, detail in RESULTS:
    print(("PASS  " if ok else "FAIL  ") + name + (("  <- " + detail) if (not ok and detail) else ""))
print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
sys.exit(1 if failed else 0)
