"""CMS settings store.

Every value is JSON-encoded so the admin panel can edit strings, numbers,
booleans and structured content (nav links, socials, FAQs) uniformly.
Public templates read through :func:`get_setting` — edits appear on the site
without any code change.
"""
from __future__ import annotations

import json
from typing import Any

from app.db import execute, query_one, now_iso

# Default public navigation — also the fallback if the CMS value is broken.
DEFAULT_NAV: list[dict[str, str]] = [
    {"href": "/", "label": "خانه"},
    {"href": "/rooms", "label": "اتاق‌ها"},
    {"href": "/rules", "label": "قوانین"},
    {"href": "/about", "label": "درباره ما"},
    {"href": "/contact", "label": "تماس با ما"},
]

# Strip of torn-paper polaroids on the homepage. Kept as a CMS value so the
# administrator can add, remove, re-caption and re-image every shot.
DEFAULT_POLAROIDS: list[dict[str, str]] = [
    {"src": "/static/img/polaroid-dardas.jpg", "caption": "دردسر", "alt": "دفتر کار در نور کم"},
    {"src": "/static/img/polaroid-khorshid.jpg", "caption": "راهروی بی‌انتها", "alt": "راهروی تاریک با نور قرمز"},
    {"src": "/static/img/polaroid-kalid.jpg", "caption": "کلید و قفل", "alt": "کلیدی قدیمی زیر نور قرمز"},
    {"src": "/static/img/polaroid-raz.jpg", "caption": "راز", "alt": "جعبه معما روی میز"},
]

# key -> default value
DEFAULTS: dict[str, Any] = {
    "nav.links": json.dumps(DEFAULT_NAV, ensure_ascii=False),
    "site.name": "اتاق فرار",
    "site.tagline": "فقط یک بازی نیست یک تجربه است ...",
    "site.description": "اتاق‌های فرار با سناریوهای واقعی و معماهای جذاب",
    "site.phone": "۰۹۱۴۰۴۵۶۸۷۷",
    "site.phone_raw": "09140456877",
    "site.address": "اصفهان، چهار باغ",
    "site.map_url": "https://nshn.ir/SbZqGYPxnYC4",
    "site.email": "info@example.com",
    "site.working_hours": "همه روزه ۱۰ صبح تا ۱۲ شب",
    "site.instagram": "https://instagram.com/matiiin141312",
    "site.telegram": "https://t.me/Matin141312",
    "site.whatsapp": "https://wa.me/",
    "site.twitter": "https://x.com/",
    "footer.copyright": "© ۱۴۰۵ تمامی حقوق محفوظ است.",
    "home.hero_title": "فقط یک بازی نیست\nیک تجربه است ...",
    "home.hero_text": "اتاق‌های فرار با سناریوهای واقعی، معماهای جذاب و معجونی از هیجان",
    "home.hero_image": "",
    "home.hero_cta": "مشاهده اتاق‌ها",
    "home.section_rooms_title": "اتاق‌های فرار ما",
    "home.section_featured_title": "اتاق‌های منتخب",
    "home.section_reviews_title": "نظرات مشتریان",
    "home.reviews_image": "",
    "home.polaroids": json.dumps(DEFAULT_POLAROIDS, ensure_ascii=False),
    "home.show_how_it_works": True,
    "pages.rules": "پیش از رزرو، لطفاً قوانین زیر را بخوانید؛ ثبت هر رزرو به معنای پذیرش این قوانین است.\n\n"
    "۱) زمان‌بندی بازی\n"
    "• هر بازی ۲ ساعت است و در بازه‌های ۱۰ تا ۱۲، ۱۴ تا ۱۶، ۱۶ تا ۱۸، ۱۸ تا ۲۰ و ۲۰ تا ۲۲ برگزار می‌شود.\n"
    "• بازه ۱۲ تا ۱۴ زمان استراحت و آماده‌سازی اتاق‌هاست و در این ساعت بازی برگزار نمی‌شود.\n"
    "• لطفاً ۱۵ دقیقه پیش از ساعت رزرو در محل حاضر باشید؛ بازی از همان ساعت رزرو شروع می‌شود و تأخیر، از زمان بازی کم می‌کند.\n\n"
    "۲) رزرو، لغو و جابه‌جایی\n"
    "• رزرو فقط از طریق همین سایت و با حساب کاربری انجام می‌شود و پس از ثبت، کد رهگیری برای شما صادر می‌شود.\n"
    "• تا ۱۲ ساعت پیش از شروع بازی، لغو یا جابه‌جایی رزرو بدون هزینه امکان‌پذیر است.\n"
    "• پس از این مهلت یا در صورت عدم حضور، هزینه رزرو بازگشت‌پذیر نیست.\n\n"
    "۳) تعداد نفرات و سن\n"
    "• تعداد نفرات باید در محدوده اعلام‌شده همان اتاق باشد؛ این محدوده در صفحه هر اتاق نوشته شده است.\n"
    "• حداقل سن بازیکنان ۱۰ سال است و برای بازیکنان زیر ۱۵ سال، حضور یک همراه بزرگسال الزامی است.\n"
    "• در صورت بارداری، بیماری قلبی، صرع، ترس شدید از تاریکی یا هر شرایط ویژه‌ای، پیش از رزرو با ما هماهنگ کنید.\n\n"
    "۴) داخل اتاق\n"
    "• رعایت نکات ایمنی و توضیحات راهنما الزامی است.\n"
    "• استفاده از تلفن همراه، دوربین، فیلم‌برداری و ضبط صدا داخل اتاق مجاز نیست؛ وسایل خود را در صندوق امانات بگذارید.\n"
    "• خوردن و آشامیدن داخل اتاق مجاز نیست.\n"
    "• باز کردن قفل‌ها و وسایل با فشار و زور، شکستن اشیا و نوشتن روی دیوارها ممنوع است؛ خسارت احتمالی بر عهده تیم بازی است.\n"
    "• اشیای اتاق را بیرون نبرید و دکمه اضطراری را فقط در شرایط واقعی فشار دهید.\n\n"
    "۵) ایمنی و رفتار\n"
    "• همه مسیرها و درها در حالت ایمن طراحی شده‌اند و راهنما در تمام مدت بازی بیرون از اتاق حاضر است.\n"
    "• اگر در حل معما گیر کردید یا احساس ناخوشی داشتید، همان لحظه به راهنما اطلاع دهید.\n"
    "• ورود در حالت مستی یا با رفتار ناسازگار با تیم، بدون بازگشت هزینه لغو می‌شود.",
    "pages.about": "ما با هدف ایجاد تجربه‌ای متفاوت و هیجان‌انگیز اتاق‌های فرار حرفه‌ای را طراحی و اجرا کردیم.",
    "pages.contact": "برای رزرو و مشاوره با ما تماس بگیرید.",
    "pages.privacy": "حریم خصوصی: تنها اطلاعات لازم برای انجام رزرو شامل نام و شماره تماس جمع‌آوری می‌شود.",
    "seo.title_suffix": " | اتاق فرار",
    "seo.robots_index": True,
    "booking.enabled": True,
    "booking.min_notice_hours": 2,
    "booking.cancel_window_hours": 12,
    "booking.currency": "تومان",
    "booking.default_price": 350000,
    "review.invite_delay_hours": 24,
    "review.auto_approve": False,
    "sound.enabled": True,
    "brand.logo_text": "ESCAPE\nROOM",
}


# Channels the footer can offer, in display order.
SOCIAL_CHANNELS: tuple[tuple[str, str], ...] = (
    ("site.instagram", "اینستاگرام"),
    ("site.telegram", "تلگرام"),
    ("site.whatsapp", "واتساپ"),
    ("site.twitter", "ایکس"),
)


def social_handle(url: Any) -> str:
    """The account part of a social URL (``https://t.me/x`` → ``x``).

    An empty result means the value is only a bare domain — ``https://t.me/``
    and friends are the shipped placeholders, and a footer button that lands on
    a network's front page leads the visitor nowhere, so it is left out.
    """
    value = str(url or "").strip()
    if not value.startswith(("http://", "https://")):
        return ""
    rest = value.split("://", 1)[1].strip("/")
    return rest.partition("/")[2].strip("/")


def get_socials() -> list[dict[str, str]]:
    """Configured social accounts for the footer, in :data:`SOCIAL_CHANNELS`
    order. Both telegram and instagram arrive this way on a seeded install."""
    out: list[dict[str, str]] = []
    for key, label in SOCIAL_CHANNELS:
        url = str(get_setting(key) or "").strip()
        handle = social_handle(url)
        if handle:
            out.append({"key": key, "label": label, "url": url, "handle": handle})
    return out


def get_setting(key: str, default: Any = None) -> Any:
    row = query_one("SELECT value FROM settings WHERE key=?", (key,))
    if row is None:
        if default is not None:
            return default
        return DEFAULTS.get(key)
    try:
        return json.loads(row["value"])
    except (TypeError, ValueError):
        return row["value"]


def parse_nav(raw: Any) -> list[dict[str, str]] | None:
    """Validate a ``nav.links`` value (JSON string or list).

    Returns a clean ``[{"href", "label"}]`` list, or ``None`` when the value
    is not usable. Items without a label, or whose href is neither ``/``-
    relative nor http(s), are dropped; the result must be non-empty.
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return None
    if not isinstance(raw, list) or not raw:
        return None
    out: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        href = str(item.get("href") or "").strip()
        label = str(item.get("label") or "").strip()
        if label and (href.startswith("/") or href.startswith(("http://", "https://"))):
            out.append({"href": href, "label": label})
    return out or None


def get_nav_links() -> list[dict[str, str]]:
    """Public nav from the CMS; falls back to the built-in list."""
    return parse_nav(get_setting("nav.links")) or [dict(i) for i in DEFAULT_NAV]


def parse_polaroids(raw: Any) -> list[dict[str, str]] | None:
    """Validate a ``home.polaroids`` value (JSON string or list).

    Every usable shot needs a src that is either site-relative (``/``) or an
    absolute http(s) URL; the caption and alt text are optional. Returns the
    clean list, or ``None`` when nothing usable was supplied.
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return None
    if not isinstance(raw, list):
        return None
    out: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        src = str(item.get("src") or "").strip()
        if not (src.startswith("/") or src.startswith(("http://", "https://"))):
            continue
        if src.startswith("//"):  # protocol-relative URLs are external too
            continue
        caption = str(item.get("caption") or "").strip()
        alt = str(item.get("alt") or "").strip() or caption
        out.append({"src": src, "caption": caption, "alt": alt})
    return out or None


def get_polaroids() -> list[dict[str, str]]:
    """Homepage polaroid strip from the CMS; falls back to the built-ins."""
    value = parse_polaroids(get_setting("home.polaroids"))
    return value if value is not None else [dict(i) for i in DEFAULT_POLAROIDS]


def get_many(keys: list[str] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in keys or list(DEFAULTS):
        out[key] = get_setting(key)
    return out


def set_setting(key: str, value: Any, admin_id: int | None = None) -> None:
    payload = json.dumps(value, ensure_ascii=False)
    execute(
        "INSERT INTO settings(key, value, updated_at, updated_by) VALUES (?,?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
        "updated_at=excluded.updated_at, updated_by=excluded.updated_by",
        (key, payload, now_iso(), admin_id),
    )


def set_many(values: dict[str, Any], admin_id: int | None = None) -> None:
    for key, value in values.items():
        set_setting(key, value, admin_id)
