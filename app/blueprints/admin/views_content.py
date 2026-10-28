"""Admin → site content editor (CMS settings), grouped by tab."""
from __future__ import annotations

from flask import flash, redirect, render_template, request, url_for

from app.auth import require_permission
from app.blueprints.admin import bp
from app.services.audit import log_action
from app.services.settings import (
    DEFAULTS,
    get_polaroids,
    get_setting,
    parse_nav,
    parse_polaroids,
    set_many,
    set_setting,
)

# tab -> (title, keys)
TABS: dict[str, tuple[str, tuple[str, ...]]] = {
    "home": (
        "صفحه اصلی",
        (
            "home.hero_title",
            "home.hero_text",
            "home.hero_cta",
            "home.hero_image",
            "home.section_rooms_title",
            "home.section_featured_title",
            "home.section_reviews_title",
            "home.show_how_it_works",
        ),
    ),
    # The polaroid strip is a structured list, so it is rendered and saved by
    # dedicated code paths (see ``polaroids_save``) instead of the flat fields.
    "polaroids": ("گالری صفحه اصلی", ()),
    "site": (
        "اطلاعات مجموعه",
        (
            "site.name",
            "site.tagline",
            "site.description",
            "site.phone",
            "site.phone_raw",
            "site.address",
            "site.map_url",
            "site.email",
            "site.working_hours",
            "site.instagram",
            "site.telegram",
            "site.whatsapp",
            "site.twitter",
            "footer.copyright",
            "brand.logo_text",
        ),
    ),
    "pages": (
        "صفحات ثابت",
        ("pages.rules", "pages.about", "pages.contact", "pages.privacy"),
    ),
    "seo": (
        "سئو",
        ("seo.title_suffix", "seo.robots_index"),
    ),
    "nav": (
        "منوی سایت",
        ("nav.links",),
    ),
}

ALL_TAB_KEYS = tuple(k for _, keys in TABS.values() for k in keys)

LABELS = {
    "home.hero_title": "عنوان اصلی (قابل شکستن خط با Enter)",
    "home.hero_text": "متن توضیح زیر عنوان",
    "home.hero_cta": "متن دکمه اصلی",
    "home.hero_image": "تصویر هیرو",
    "home.section_rooms_title": "عنوان بخش اتاق‌ها",
    "home.section_featured_title": "زیرعنوان بخش اتاق‌ها",
    "home.section_reviews_title": "عنوان بخش نظرات",
    "home.show_how_it_works": "نمایش بخش نظرات در صفحه اصلی",
    "site.name": "نام مجموعه",
    "site.tagline": "شعار",
    "site.description": "توضیح کوتاه (سئو)",
    "site.phone": "تلفن (نمایشی)",
    "site.phone_raw": "تلفن (لینک تماس)",
    "site.address": "آدرس",
    "site.map_url": "لینک نقشه (اختیاری)",
    "site.email": "ایمیل",
    "site.working_hours": "ساعات کاری",
    "site.instagram": "اینستاگرام",
    "site.telegram": "تلگرام",
    "site.whatsapp": "واتساپ",
    "site.twitter": "ایکس (توییتر)",
    "footer.copyright": "متن کپی‌رایت فوتر",
    "brand.logo_text": "متن لوگو",
    "pages.rules": "صفحه قوانین",
    "pages.about": "صفحه درباره ما",
    "pages.contact": "صفحه تماس با ما",
    "pages.privacy": "صفحه حریم خصوصی",
    "seo.title_suffix": "پسوند عنوان صفحات",
    "seo.robots_index": "اجازه ایندکس شدن سایت",
    "nav.links": "لینک‌های منو — JSON، مثلاً [{\"href\": \"/\", \"label\": \"خانه\"}]",
}

# Fields whose value is an image path: the editor offers the media library
# picker next to them so an admin never has to paste a URL by hand.
IMAGE_KEYS = {"home.hero_image", "home.reviews_image"}

BOOLEANS = {"home.show_how_it_works", "seo.robots_index"}
TEXTAREAS = {
    "home.hero_text",
    "home.hero_title",
    "pages.rules",
    "pages.about",
    "pages.contact",
    "pages.privacy",
    "footer.copyright",
    "nav.links",
}


def _tab_keys(tab: str) -> tuple[str, ...]:
    return TABS.get(tab, TABS["home"])[1]


def _media_options() -> list[dict]:
    """Everything in the media library, for the image picker."""
    from app.db import query

    rows = query(
        "SELECT id, filename, path FROM media ORDER BY created_at DESC LIMIT 500"
    )
    return [dict(r) for r in rows]


@bp.route("/content")
@bp.route("/content/<tab>")
@require_permission("content.edit")
def content_index(tab: str = "home"):
    tab = tab if tab in TABS else "home"
    keys = _tab_keys(tab)
    values = {k: get_setting(k) for k in keys}
    return render_template(
        "admin/content/index.html",
        tab=tab,
        tabs={k: title for k, (title, _) in TABS.items()},
        keys=keys,
        values=values,
        labels=LABELS,
        textareas=TEXTAREAS,
        booleans=BOOLEANS,
        image_keys=IMAGE_KEYS,
        media=_media_options(),
        polaroids=get_polaroids(),
        defaults=DEFAULTS,
    )


@bp.route("/content/polaroids", methods=["POST"])
@require_permission("content.edit")
def polaroids_save():
    """Save the homepage polaroid strip (add / edit / remove rows at once).

    The form posts parallel ``src[] / caption[] / alt[]`` arrays; a row whose
    src is blank is treated as removed. Nothing usable at all restores the
    built-in strip rather than leaving the homepage with an empty band.
    """
    srcs = request.form.getlist("polaroid_src")
    captions = request.form.getlist("polaroid_caption")
    alts = request.form.getlist("polaroid_alt")

    items = []
    for i, src in enumerate(srcs):
        items.append(
            {
                "src": src,
                "caption": captions[i] if i < len(captions) else "",
                "alt": alts[i] if i < len(alts) else "",
            }
        )
    if not [i for i in items if (i["src"] or "").strip()]:
        # Every row was emptied — clear the setting so the defaults return.
        set_many({"home.polaroids": ""})
        log_action("content.polaroids", "بازگشت به گالری پیش‌فرض", entity_type="settings")
        flash("گالری به حالت پیش‌فرض بازگشت.", "success")
        return redirect(url_for("admin.content_index", tab="polaroids"))

    parsed = parse_polaroids(items)
    if parsed is None:
        flash("هر تصویر باید یک مسیر معتبر داشته باشد.", "error")
        return redirect(url_for("admin.content_index", tab="polaroids"))

    set_setting("home.polaroids", parsed)
    log_action("content.polaroids", f"{len(parsed)} تصویر", entity_type="settings")
    flash("گالری صفحه اصلی ذخیره شد.", "success")
    return redirect(url_for("admin.content_index", tab="polaroids"))


@bp.route("/content", methods=["POST"])
@require_permission("content.edit")
def content_save():
    tab = request.form.get("tab", "home")
    if tab not in TABS:
        tab = "home"

    # A "پاک کردن" checkbox on a field asks explicitly for the value to be
    # emptied — the one case where a blank input is meant to erase content.
    cleared = set(request.form.getlist("__clear"))

    payload: dict = {}
    for key in _tab_keys(tab):
        if key in BOOLEANS:
            # An unchecked checkbox is absent from the POST body, so the form
            # submits a hidden "0" twin right before it and the LAST value
            # wins. A key the caller did not send at all (a partial POST —
            # e.g. saving just two fields of the tab) is skipped instead of
            # silently switching the toggle off.
            values = request.form.getlist(key)
            if not values:
                continue
            payload[key] = values[-1] in ("1", "true", "True", "on", "yes")
            continue
        if key not in request.form:
            continue
        raw = request.form.get(key, "")
        value = raw.strip()
        if key in cleared:
            payload[key] = ""
            continue
        # An empty textarea must not wipe a required page body by accident —
        # the explicit "پاک کردن" flag above is how a value is erased.
        if key in TEXTAREAS and not value:
            continue
        if key == "nav.links" and parse_nav(value) is None:
            flash(
                "فرمت JSON منو نامعتبر است؛ باید آرایه‌ای از {\"href\": ..., \"label\": ...} باشد.",
                "error",
            )
            return redirect(url_for("admin.content_index", tab=tab))
        payload[key] = value

    set_many(payload)
    log_action("content.update", f"{tab}: {', '.join(payload)}", entity_type="settings")
    flash("محتوای سایت ذخیره شد.", "success")
    return redirect(url_for("admin.content_index", tab=tab))