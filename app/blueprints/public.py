"""Public website: home, rooms, detail, booking, reviews, static pages."""
from __future__ import annotations

from datetime import date, timedelta

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from app.db import query, query_one
from app.services import availability
from app.services.customer_auth import current_customer
from app.services.settings import get_setting

bp = Blueprint("public", __name__)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _published():
    return query(
        "SELECT * FROM scenarios WHERE status='published' ORDER BY featured DESC, sort_order, title"
    )


def _scenario_or_404(slug: str):
    row = query_one("SELECT * FROM scenarios WHERE slug=?", (slug,))
    if row is None or row["status"] not in ("published", "inactive"):
        abort(404)
    return row


def _images(scenario_id: int):
    return query(
        "SELECT * FROM scenario_images WHERE scenario_id=? ORDER BY is_cover DESC, sort_order",
        (scenario_id,),
    )


def _cover(scenario) -> str | None:
    if scenario["cover_image"]:
        return scenario["cover_image"]
    imgs = _images(scenario["id"])
    return imgs[0]["path"] if imgs else None


def _home_setting(key: str, default=None):
    """Read a homepage CMS setting, falling back to the packaged default."""
    value = get_setting(key, None)
    if value is None or value == "":
        from app.services.settings import DEFAULTS

        value = DEFAULTS.get(key, default)
    return value


def _scenario_card(scenario) -> dict:
    from app.services.reviews import stats

    s = dict(scenario)
    s["cover"] = _cover(scenario)
    s["stats"] = stats(scenario["id"])
    # The admin's per-room switch travels with the card so every list can show
    # a closed room as closed instead of offering a button that will refuse.
    s["bookable"] = availability.room_bookable(scenario)
    return s


# --------------------------------------------------------------------------
# Pages
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Home
# --------------------------------------------------------------------------

# The torn-paper strip between the polaroids and the section title now lives
# in the CMS (``home.polaroids``) so the administrator can add, reorder by
# editing, re-caption and re-image every shot without a deploy.


@bp.route("/")
def home():
    rooms = [_scenario_card(s) for s in _published()]
    featured = [r for r in rooms if r["featured"]] or rooms
    from app.services.reviews import stats, list_reviews
    from app.services.settings import get_polaroids

    return render_template(
        "public/home.html",
        rooms=rooms,
        featured=featured,
        polaroids=get_polaroids(),
        review_stats=stats(),
        reviews=list_reviews(limit=6),
        hero_image=get_setting("home.hero_image") or "/static/img/hero.jpg",
        hero_title=_home_setting("home.hero_title"),
        hero_text=_home_setting("home.hero_text"),
        hero_cta=_home_setting("home.hero_cta"),
        section_rooms_title=_home_setting("home.section_rooms_title"),
        section_featured_title=_home_setting("home.section_featured_title"),
        section_reviews_title=_home_setting("home.section_reviews_title"),
        show_how_it_works=bool(_home_setting("home.show_how_it_works", True)),
        settings=get_setting,
    )


@bp.route("/rooms")
def rooms():
    status = request.args.get("status", "")
    genre = (request.args.get("genre") or "").strip()
    rows = _published()
    if status == "open":
        rows = [r for r in rows if r["status"] == "published"]
    if genre:
        rows = [r for r in rows if (r["genre"] or "") == genre]

    # Build the genre chips from live data so a genre added in the admin panel
    # shows up here without a code change.
    genres = []
    for row in _published():
        name = row["genre"]
        if name and name not in genres:
            genres.append(name)

    return render_template(
        "public/rooms.html",
        rooms=[_scenario_card(s) for s in rows],
        filters={"status": status, "genre": genre},
        genres=genres,
        section_rooms_title=_home_setting("home.section_rooms_title"),
        section_featured_title=_home_setting("home.section_featured_title"),
    )


@bp.route("/rooms/<slug>")
def room_detail(slug: str):
    scenario = _scenario_or_404(slug)
    card = _scenario_card(scenario)
    images = _images(scenario["id"])
    from app.services.reviews import stats, list_reviews

    similar = [
        _scenario_card(s)
        for s in _published()
        if s["id"] != scenario["id"] and s["genre"] == scenario["genre"]
    ][:3]
    if not similar:
        similar = [_scenario_card(s) for s in _published() if s["id"] != scenario["id"]][:3]

    import json

    try:
        faqs = json.loads(scenario["faqs"] or "[]")
    except ValueError:
        faqs = []

    # The booking form itself lives on its own page (/book/<slug>); the detail
    # page only links to it. Nothing here needs the slot calendar any more, so
    # the page no longer materialises 45 days of slots on every view.
    return render_template(
        "public/room_detail.html",
        scenario=scenario,
        card=card,
        images=images,
        cover=_cover(scenario),
        faqs=faqs,
        similar=similar,
        review_stats=stats(scenario["id"]),
        reviews=list_reviews(scenario_id=scenario["id"], limit=8),
        booking_enabled=bool(get_setting("booking.enabled", True)),
        bookable=availability.room_bookable(scenario),
        customer=current_customer(),
        share_url=f"{current_app.config['SITE_URL']}{url_for('public.room_detail', slug=slug)}",
    )


@bp.route("/rules")
def rules():
    return render_template("public/page.html", title="قوانین و مقررات", body=get_setting("pages.rules"))


@bp.route("/about")
def about():
    return render_template("public/page.html", title="درباره ما", body=get_setting("pages.about"))


# Shown at the very bottom of the contact page only.
CREDIT = "created by Matin Alinghian"


@bp.route("/contact")
def contact():
    addr = get_setting("site.address")
    # Admin-chosen link wins; otherwise derive a مسیریاب نشان (Neshan) search URL.
    map_url = get_setting("site.map_url") or ""
    if addr and not map_url:
        from app.services.neshan import neshan_search_url

        map_url = neshan_search_url(addr)
    contact_rows = [
        ("تلفن", get_setting("site.phone"), f"tel:{get_setting('site.phone_raw')}" if get_setting("site.phone_raw") else None),
        ("آدرس", addr, map_url if addr else None),
        ("ساعات کاری", get_setting("site.working_hours"), None),
    ]
    return render_template(
        "public/page.html",
        title="تماس با ما",
        body=get_setting("pages.contact"),
        contact_rows=[(label, value, href) for label, value, href in contact_rows if value],
        credit=CREDIT,
    )


@bp.route("/privacy")
def privacy():
    return render_template("public/page.html", title="حریم خصوصی", body=get_setting("pages.privacy"))


# --------------------------------------------------------------------------
# Booking flow
# --------------------------------------------------------------------------


def _split_name(full_name: str) -> tuple[str, str]:
    """Split an account name into (first, family) for the booking form."""
    parts = (full_name or "").split()
    if not parts:
        return "", ""
    return parts[0], " ".join(parts[1:])


@bp.route("/book/<slug>", methods=["GET", "POST"])
def book(slug: str):
    scenario = _scenario_or_404(slug)
    if not get_setting("booking.enabled", True):
        flash("رزرو آنلاین موقتاً غیرفعال است.", "error")
        return redirect(url_for("public.room_detail", slug=slug))

    # The room's own switch comes first: "this room is closed" is a clearer
    # answer than "sign in first", and it holds for the POST as well.
    if not availability.room_bookable(scenario):
        flash("این اتاق موقتاً بسته است و رزرو نمی‌پذیرد.", "error")
        return redirect(url_for("public.room_detail", slug=slug))

    # Booking is account-only: a guest is sent to the login page first and
    # returns here the moment they are signed in (``?next=``).
    customer = current_customer()
    if customer is None:
        flash("برای رزرو آنلاین ابتدا وارد حساب کاربری خود شوید.", "info")
        return redirect(
            url_for("account.unified_login", next=url_for("public.book", slug=slug))
        )

    slots: list[dict] = []
    selected_date = request.values.get("date") or availability.business_today().isoformat()
    try:
        selected = date.fromisoformat(selected_date)
    except ValueError:
        selected = availability.business_today()
        selected_date = selected.isoformat()

    if selected < availability.business_today():
        selected = availability.business_today()
        selected_date = selected.isoformat()

    slots = availability.slots_for_date(scenario["id"], selected)

    if request.method == "POST":
        try:
            slot_id = int(request.form.get("slot_id", 0))
            reservation = availability.create_reservation(
                scenario_id=scenario["id"],
                slot_id=slot_id,
                participants=request.form.get("participants", 1),
                name=request.form.get("name") or "",
                first_name=request.form.get("first_name") or "",
                family_name=request.form.get("family_name") or "",
                phone=(request.form.get("phone") or "").strip(),
                email=(request.form.get("email") or "").strip(),
                note=(request.form.get("note") or "").strip(),
                # The reservation belongs to the signed-in account, whatever
                # phone number the browser happened to post.
                customer_id=customer["id"],
            )
        except availability.BookingError as exc:
            flash(str(exc), "error")
        else:
            return redirect(url_for("public.confirmation", code=reservation["code"]))

    min_p = int(scenario.get("min_players") or 1)
    max_p = int(scenario.get("max_players") or min_p)

    # The visible upper bound must never exceed what any open slot on this day can
    # still accept. Otherwise the visitor is told a number is fine and then refused
    # by the server-side check for a slot whose remaining capacity is already lower.
    if slots:
        remain = [int(s["capacity"]) - int(availability.seats_taken(s["id"])) for s in slots]
        if remain:
            available_cap = min(remain)
            if available_cap < max_p:
                max_p = max(min_p, available_cap)

    first_name, family_name = _split_name(customer["name"])
    return render_template(
        "public/booking.html",
        scenario=scenario,
        cover=_cover(scenario),
        slots=slots,
        selected_date=selected_date,
        min_players=min_p,
        max_players=max_p,
        range_days=45,
        currency_label=get_setting("booking.currency", "تومان"),
        customer=customer,
        prefill={
            "first_name": first_name,
            "family_name": family_name,
            "phone": customer["phone"] or "",
            "email": customer["email"] or "",
        },
    )


@bp.route("/booking/<code>")
def confirmation(code: str):
    reservation = availability.get_reservation(code)
    if reservation is None:
        abort(404)
    return render_template("public/confirmation.html", r=reservation)


@bp.route("/payment/callback", methods=["GET", "POST"])
def payment_callback():
    """Gateway redirect target — status is always verified server side."""
    reference = request.values.get("reference") or request.values.get("Authority") or ""
    from app.services import payments

    try:
        status = payments.verify(reference)
    except payments.PaymentError:
        flash("پرداخت قابل تأیید نیست.", "error")
        return redirect(url_for("public.home"))
    if status == "paid":
        flash("پرداخت با موفقیت انجام شد.", "success")
    else:
        flash("پرداخت هنوز تأیید نشده است.", "error")
    res = query_one("SELECT code FROM payment_transactions WHERE reference=?", (reference,))
    if res:
        return redirect(url_for("public.confirmation", code=res["code"]))
    return redirect(url_for("public.home"))


# --------------------------------------------------------------------------
# Reviews
# --------------------------------------------------------------------------


@bp.route("/review/<code>", methods=["GET", "POST"])
def review_form(code: str):
    from app.services.reviews import ReviewError, eligibility, submit

    info = eligibility(code)
    if request.method == "POST":
        try:
            result = submit(
                code,
                request.form.get("rating", 0),
                request.form.get("title", ""),
                request.form.get("body", ""),
            )
        except ReviewError as exc:
            flash(str(exc), "error")
        else:
            msg = "نظر شما ثبت شد و پس از تأیید نمایش داده می‌شود." if result["status"] == "pending" else "نظر شما منتشر شد."
            flash(msg, "success")
            return redirect(url_for("public.room_detail", slug=info.get("scenario_slug", "")))
    return render_template("public/review_form.html", info=info, code=code)


@bp.route("/reviews")
def reviews_page():
    from app.services.reviews import list_reviews, stats

    sort = request.args.get("sort", "newest")
    page = max(int(request.args.get("page", 1) or 1), 1)
    per = 12

    # A room page sends the visitor here with ?room=<slug> — "همه نظرات این
    # اتاق" — and this page then shows that room's reviews and its average
    # only. Without the filter it listed every room's reviews at once, which is
    # not what the link promises.
    room = None
    room_slug = (request.args.get("room") or "").strip()
    if room_slug:
        found = query_one("SELECT id, slug, title FROM scenarios WHERE slug=?", (room_slug,))
        if found is not None:
            room = dict(found)

    scenario_id = room["id"] if room else None
    reviews = list_reviews(
        scenario_id=scenario_id, sort=sort, limit=per, offset=(page - 1) * per
    )
    return render_template(
        "public/reviews.html",
        reviews=reviews,
        stats=stats(scenario_id),
        sort=sort,
        page=page,
        room=room,
        has_more=len(reviews) == per,
    )


# --------------------------------------------------------------------------
# SEO
# --------------------------------------------------------------------------


@bp.route("/sitemap.xml")
def sitemap():
    base = current_app.config["SITE_URL"]
    urls = [
        ("/", "daily", "1.0"),
        ("/rooms", "weekly", "0.9"),
        ("/rules", "monthly", "0.4"),
        ("/about", "monthly", "0.4"),
        ("/contact", "monthly", "0.4"),
        ("/reviews", "weekly", "0.6"),
    ]
    for row in query("SELECT slug, updated_at FROM scenarios WHERE status='published'"):
        urls.append((f"/rooms/{row['slug']}", "weekly", "0.8"))
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for path, freq, priority in urls:
        parts.append(
            f"  <url><loc>{base}{path}</loc><changefreq>{freq}</changefreq>"
            f"<priority>{priority}</priority></url>"
        )
    parts.append("</urlset>")
    return "\n".join(parts), 200, {"Content-Type": "application/xml; charset=utf-8"}


@bp.route("/robots.txt")
def robots():
    base = current_app.config["SITE_URL"]
    body = f"""User-agent: *
Allow: /
Disallow: /admin
Disallow: /api/
Sitemap: {base}/sitemap.xml
"""
    return body, 200, {"Content-Type": "text/plain; charset=utf-8"}


@bp.route("/ads.txt")
def ads_txt():
    return "google.com, pub-0000000000000000, DIRECT, f08c47fec0942fa0", 200, {
        "Content-Type": "text/plain; charset=utf-8"
    }
