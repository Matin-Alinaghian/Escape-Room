"""Idempotent seed data for development / demos.

Run via:  python manage.py seed
"""
from __future__ import annotations

import json
from datetime import date, timedelta

from app.auth import hash_password
from app.db import execute, query, query_one
from app.jalali import fa_digits
from app.services.settings import DEFAULTS, set_many


def seed() -> None:
    _migrate_customers()
    _seed_admin()
    _seed_scenarios()
    _seed_schedules()
    _seed_settings()
    _seed_templates()


def _seed_admin() -> None:
    # Secondary (staff) account is ensured on every seed run so the two-level
    # admin panel stays testable even on databases that already have a super.
    execute(
        "INSERT OR IGNORE INTO admins(username,password_hash,display_name,role) VALUES (?,?,?,?)",
        ("staff", hash_password("Staff12345678"), "کارمند", "secondary"),
    )
    staff_row = query_one("SELECT id FROM admins WHERE username='staff'")
    staff_id = staff_row["id"] if staff_row else 1
    super_row = query_one("SELECT id FROM admins WHERE role='super'")
    grantor = super_row["id"] if super_row else staff_id
    for p in ("dashboard.view", "bookings.view", "customers.view"):
        execute(
            "INSERT OR IGNORE INTO admin_permissions(admin_id, permission, granted_by) VALUES (?,?,?)",
            (staff_id, p, grantor),
        )
    if super_row:
        return
    execute(
        "INSERT INTO admins(username,password_hash,display_name,role) VALUES (?,?,?,?)",
        ("admin", hash_password("Admin12345678"), "مدیر اصلی", "super"),
    )
    super_id = query_one("SELECT id FROM admins WHERE role='super'")["id"]
    perms = [
        "dashboard.view",
        "scenarios.manage",
        "media.manage",
        "schedules.manage",
        "bookings.view",
        "bookings.edit",
        "bookings.cancel",
        "bookings.complete",
        "customers.view",
        "reviews.view",
        "reviews.moderate",
        "content.edit",
        "reports.view",
        "export",
        "notifications.manage",
        "logs.view",
    ]
    for p in perms:
        execute(
            "INSERT OR IGNORE INTO admin_permissions(admin_id, permission, granted_by) VALUES (?,?,?)",
            (super_id, p, super_id),
        )


def _seed_scenarios() -> None:
    if query_one("SELECT id FROM scenarios LIMIT 1"):
        return

    scenarios = [
        {
            "slug": "atakh-bimaristan",
            "title": "اتاق بیمارستان",
            "short_description": "بیمارستانی متروک که هر شب ساعت ۳ باز می‌شود...",
            "story": (
                "نیمه‌شب، بخش قدیمی بیمارستان به سکوت کامل فرو می‌رود؛ تنها صدای قدم‌هایی که "
                "در راهروهای متروک می‌پیچد. بیمارانی که سال‌ها پیش ناپدید شده‌اند، دوباره در "
                "پرونده‌ها ظاهر شده‌اند. تا پیش از سپیده‌دم باید اتاق شماره ۱۳ را پیدا کنید و "
                "بیمار گم‌شده را بیرون بیاورید."
            ),
            "genre": "وحشت",
            "horror_intensity": 4,
            "difficulty": 3,
            "duration_minutes": 120,
            "min_players": 2,
            "max_players": 4,
            "price_per_person": 350000,
            "price_per_group": 0,
            "discount_percent": 0,
            "address": "تهران، خیابان آزادی، پلاک ۱۲",
            "age_restriction": "۱۲ سال به بالا",
            "safety": "مسیری کم‌پروا و ایمن. هرگز تنها نمانید.",
            "rules": "رعایت نکات ایمنی الزامی است. استفاده از تلفن همراه ممنوع است.",
            "faqs": json.dumps(
                [{"q": "آیا برای بازی نیاز به تجربه قبلی دارید؟","a": "خیر، هیچ تجربه قبلی لازم نیست. در آغاز هر بازی، بازیکنان به‌طور کامل راهنمایی می‌شوند و قوانین به‌طور گسترده توضیح داده می‌شود. حتی اگر اولین باری است، تیم شما راهنمایی می‌کند و اگر در زمینه‌ای مشکل کردید، راهنما آماده کمک است. برای هرگونه سوال یا مشکلات، با مدیر راهنما و مشاور خود مشورت کنید و هرچه سریع‌تر اقدام نمایید."},{"q": "چه ساعتی بازی برگزار می‌شود؟","a": "بازی‌ها هر روز از ساعت ۱۰ صبح تا ۱۰ شب برگزار می‌شوند. هر زمان که می‌خواهید می‌توانید رزرو کنید، فقط کافی است در فرم رزرو تاریخ و ساعت را انتخاب کنید. پس از تایید، کد رزرو به شماره تلفن یا ایمیل شما ارسال می‌شود."},{"q": "سن مناسب بازی چیست؟","a": "اتاق‌های فرار برای افراد بالای ۱۲ سال طراحی شده‌اند. برای سن‌های پایین‌تر نیز اتاق‌های مخصوص کودک وجود دارد. اگر کودک شما زیر ۱۲ سال هست، می‌توانید به عنوان مشاور در بازی مشارکت کنید."},{"q": "چطور می‌توانم رزرو کنم؟","a": "کافیست روی دکمه «رزرو آنلاین» کلیک کنید، تاریخ و زمان دلخواه خود را انتخاب کنید و فرم را پر کنید. پس از تایید، کد رزرو به شماره تلفن یا ایمیل شما ارسال می‌شود."},{"q": "آیا می‌توانم لغو کنم؟","a": "بله، درخواست‌های رزرو تا ۱۲ ساعت قبل از شروع بازی می‌توانند لغو شوند. در غیر این صورت، هزینه کل محاسبه می‌شود. لطفاً در صورت نیاز سریعاً اقدام کنید."}]
            ),
            "featured": 1,
            "status": "published",
        },
        {
            "slug": "atakh-jadugar",
            "title": "اتاق جادوگر",
            "short_description": "برجی متروک، طلسمی نیمه‌کاره و آینه‌ای که هرگز راست نمی‌گوید...",
            "story": (
                "جادوگری برای آشکار کردن راز دشمنش خود را در برجی متروک حبس کرده است. "
                "دفترهای پراکنده، آینه‌ای که هرگز تصویر درست نشان نمی‌دهد و نشانه‌هایی که "
                "باید کنار هم بچینید تا طلسم بشکند و راه خروج پیدا شود."
            ),
            "genre": "جادو",
            "horror_intensity": 3,
            "difficulty": 4,
            "duration_minutes": 120,
            "min_players": 4,
            "max_players": 6,
            "price_per_person": 437500,
            "price_per_group": 1750000,
            "discount_percent": 0,
            "address": "تهران، خیابان آزادی، پلاک ۱۲",
            "age_restriction": "۱۰ سال به بالا",
            "safety": "مسیر پیشنهادی ما کاملاً ایمن است. در صورت تمایل می‌توانید از راهنمای داخل اتاق استفاده کنید.",
            "rules": "وسایل شخصی و کیسه همراه خود را در سمت چپ راهرو قرار دهید.",
            "faqs": json.dumps([
                {"q": "بازی برای چند نفر است؟", "a": "بین ۴ تا ۶ نفر."},
                {"q": "سختی بازی چقدر است؟", "a": "سطح متوسط رو به بالا؛ برای تیم‌های باتجربه مناسب است."},
            ]),
            "featured": 1,
            "status": "published",
        },
        {
            "slug": "atakh-zendan",
            "title": "اتاق زندان",
            "short_description": "زندانی خالی که دوباره پر شده است...",
            "story": (
                "زندانی که سال‌ها خالی مانده بود، دوباره پر شده است. تنها یک راه بیرون رفتن "
                "وجود دارد: پیدا کردن پنج نشانه‌ای که نگهبانان از آن‌ها پاس می‌دادند و "
                "چیدن آن‌ها به ترتیبی که روی دیوار حکاکی شده است."
            ),
            "genre": "زندان",
            "horror_intensity": 3,
            "difficulty": 3,
            "duration_minutes": 120,
            "min_players": 5,
            "max_players": 5,
            "price_per_person": 400000,
            "price_per_group": 0,
            "discount_percent": 10,
            "address": "تهران، خیابان آزادی، پلاک ۱۲",
            "age_restriction": "۱۵ سال به بالا",
            "safety": "این سناریو کم‌ترس طراحی شده و برای همه مناسب است.",
            "rules": "با دقت به جزئیات دیوارها نگاه کنید؛ سرنخ‌ها همان‌جا هستند.",
            "faqs": json.dumps([
                {"q": "آیا نوجوان می‌تواند بازی کند؟", "a": "خیر، این اتاق حداقل ۱۵ سال دارد."},
                {"q": "ظرفیت اتاق چند نفر است؟", "a": "دقیقاً ۵ نفر."},
            ]),
            "featured": 1,
            "status": "published",
        },
        {
            "slug": "atakh-nefari",
            "title": "اتاق نفرین",
            "short_description": "خانه‌ای در لبه جنگل که نفرین‌ها بالاخره جواب داده‌اند...",
            "story": (
                "نفرین‌ها بالاخره جواب داده‌اند. خانه‌ای قدیمی در لبه جنگل، پر از نشانه‌هایی که "
                "هیچ‌کدامتصادفی نیستند. سه ساعت وقت دارید تا بفهمید چه چیزی در این خانه بسته "
                "شده و راه بیرون رفتن را پیدا کنید."
            ),
            "genre": "وحشت",
            "horror_intensity": 5,
            "difficulty": 5,
            "duration_minutes": 120,
            "min_players": 4,
            "max_players": 6,
            "price_per_person": 450000,
            "price_per_group": 0,
            "discount_percent": 5,
            "address": "تهران، خیابان آزادی، پلاک ۱۲",
            "age_restriction": "۱۸ سال به بالا",
            "safety": "بازی شامل صحنه‌های ترسناک و صدای بلند است. برای افراد حساس توصیه نمی‌شود.",
            "rules": "در اتاق فریاد نزنید؛ راهنما نزدیک شما است.",
            "faqs": json.dumps([
                {"q": "بازی چقدر ترسناک است؟", "a": "بیشترین شدت وحشت در میان سناریوهای ما."},
                {"q": "چه ساعتی بازی می‌شود؟", "a": "هر روز از ۱۰ صبح تا ۱۰ شب."},
            ]),
            "featured": 1,
            "status": "published",
        },
        {
            "slug": "atakh-khoon",
            "title": "اتاق خون",
            "short_description": "جایی که دیوارها همیشه تازه شسته شده‌اند... ولی لکه‌ها هرگز پاک نمی‌شوند.",
            "story": (
                "کارگر سالن دیگر بعد از واقعه آن شب برنگشت. دیوارها را هر هفته می‌شویند و هر هفته لکه‌ها دوباره سر جایشان می‌نشینند.\n"
                "می‌گویند از زیر تخت‌ها صدای چکه می‌آید؛ نه صدای آب، بلکه صدای چیزی که هنوز پیدا نشده. آخرین گروهی که بازی کردند، سوئیچ برق را با برچسب «نچسبانید» پیدا کردند — و برچسب از داخل چسبیده بود."
            ),
            "genre": "خون",
            "horror_intensity": 5,
            "difficulty": 4,
            "duration_minutes": 120,
            "min_players": 2,
            "max_players": 5,
            "price_per_person": 420000,
            "price_per_group": 0,
            "discount_percent": 0,
            "address": "تهران، خیابان آزادی، پلاک ۱۲",
            "age_restriction": "۱۶ سال به بالا",
            "safety": (
                "بازی با نور کم و صحنه‌سازی انجام می‌شود؛ هیچ خون واقعی در فضا وجود ندارد.\n"
                "لکه‌های خون بخشی از دکور هستند؛ دست نزنید و از جاکندها فاصله بگیرید. "
                "در هر لحظه می‌توانید با زدن دو ضربه به در، بازی را متوقف کنید؛ راهنما بلافاصله وارد می‌شود."
            ),
            "rules": (
                "گوشی موبایل باید قبل از شروع، داخل کمد تحویل داده شود.\n"
                "روی لکه‌های خون راه نروید؛ جای پا علامت‌های راهنما را می‌پوشاند. "
                "قطعات دکور را جابه‌جا نکنید؛ هر قطعه در پازل نقش دارد."
            ),
            "faqs": json.dumps([
                {"q": "آیا خون واقعی وجود دارد؟", "a": "خیر؛ همه لکه‌ها دکور رنگ‌شده‌اند و پس از هر بازی ضدعفونی می‌شوند."},
                {"q": "بازی چقدر ترسناک است؟", "a": "شدیدترین سناریوی مجموعه است؛ برای افراد زیر ۱۶ سال توصیه نمی‌شود."},
                {"q": "اگر بترسم می‌توانم خارج شوم؟", "a": "بله؛ در همیشه از داخل باز می‌شود و می‌توانید هر لحظه خروج اضطراری بزنید."},
            ]),
            "featured": 1,
            "status": "published",
        },
    ]

    for s in scenarios:
        s["cover_image"] = _cover_for(s["slug"])
        execute(
            "INSERT INTO scenarios(slug,title,short_description,story,genre,horror_intensity,difficulty,"
            "duration_minutes,min_players,max_players,price_per_person,price_per_group,discount_percent,address,age_restriction,"
            "safety,rules,faqs,featured,status,seo_title,seo_description,sort_order,cover_image) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                s["slug"],
                s["title"],
                s["short_description"],
                s["story"],
                s["genre"],
                s["horror_intensity"],
                s["difficulty"],
                s["duration_minutes"],
                s["min_players"],
                s["max_players"],
                s["price_per_person"],
                s["price_per_group"],
                s["discount_percent"],
                s["address"],
                s["age_restriction"],
                s["safety"],
                s["rules"],
                s["faqs"],
                s["featured"],
                s["status"],
                (s["title"] + " — اتاق فرار"),
                s["short_description"],
                scenarios.index(s),
                s["cover_image"],
            ),
        )
        _seed_images(s["slug"], s["title"], s["cover_image"])


def _seed_images(slug: str, title: str, cover: str | None) -> None:
    """Attach a cover plus any generated gallery shots to a scenario."""
    row = query_one("SELECT id FROM scenarios WHERE slug=?", (slug,))
    if row is None:
        return
    scenario_id = row["id"]

    from app.config import BASE_DIR

    uploads = BASE_DIR / "app" / "static" / "uploads" / "scenarios"
    shots = []
    if cover:
        shots.append((cover, "تصویر اصلی " + title, 0, 1))
    for index in range(1, 5):
        candidate = uploads / f"gallery-{slug}-{index}.jpg"
        if not candidate.is_file():
            continue
        shots.append(
            (
                f"/static/uploads/scenarios/gallery-{slug}-{index}.jpg",
                f"نمای {['اول', 'دوم', 'سوم', 'چهارم'][index - 1]} از {title}",
                index,
                0,
            )
        )

    for path, alt, sort_order, is_cover in shots:
        execute(
            "INSERT INTO scenario_images(scenario_id,path,alt,sort_order,is_cover) VALUES (?,?,?,?,?)",
            (scenario_id, path, alt, sort_order, is_cover),
        )


def _cover_for(slug: str) -> str | None:
    """Point a scenario at its generated cover, when the artwork exists."""
    from pathlib import Path

    from app.config import BASE_DIR

    candidate = BASE_DIR / "app" / "static" / "img" / f"{slug}.jpg"
    if candidate.is_file():
        return f"/static/img/{slug}.jpg"
    return None


def _seed_schedules() -> None:
    if query_one("SELECT id FROM schedules LIMIT 1"):
        return
    # Every room plays 2-hour sessions: 10–12, then 14–16, 16–18, 18–20 and
    # 20–22. The 12–14 lunch break is the gap between these two opening
    # windows, so it never becomes a slot the visitor could pick.
    windows = (("10:00", "12:00"), ("14:00", "22:00"))
    scenario_ids = [r["id"] for r in query("SELECT id FROM scenarios WHERE status='published' ORDER BY id")]
    for sid in scenario_ids:
        for wd in range(7):
            for open_time, close_time in windows:
                execute(
                    "INSERT OR IGNORE INTO schedules(scenario_id,weekday,open_time,close_time,slot_minutes,capacity,price_override,valid_from,valid_to) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (sid, wd, open_time, close_time, 120, 6, None, None, None),
                )


def _seed_settings() -> None:
    defaults = {
        "site.name": "اتاق فرار",
        "site.tagline": "فقط یک بازی نیست یک تجربه است ...",
        "site.description": "اتاق‌های فرار با سناریوهای واقعی و معماهای جذاب",
        "site.phone": "۰۹۱۴۰۴۵۶۸۷۷",
        "site.phone_raw": "09140456877",
        "site.address": "تهران، خیابان آزادی، پلاک ۱۲",
        "site.email": "info@example.com",
        "site.working_hours": "همه روزه ۱۰ صبح تا ۱۲ شب",
        "site.instagram": "https://instagram.com/matiiin141312",
        "site.telegram": "https://t.me/Matin141312",
        "site.whatsapp": "https://wa.me/",
        "site.twitter": "https://x.com/",
        "footer.copyright": fa_digits(1405) + " تمامی حقوق محفوظ است.",
        "home.hero_title": "فقط یک بازی نیست\nیک تجربه است ...",
        "home.hero_text": "اتاق‌های فرار با سناریوهای واقعی، معماهای جذاب و معجونی از هیجان",
        "home.hero_image": "/static/img/hero.jpg",
        "home.hero_cta": "مشاهده اتاق‌ها",
        "home.section_rooms_title": "اتاق‌های فرار ما",
        "home.section_featured_title": "اتاق‌های منتخب",
        "home.section_reviews_title": "نظرات مشتریان",
        "home.show_how_it_works": True,
        # One source of truth for the long rules page: the shipped default.
        "pages.rules": DEFAULTS["pages.rules"],
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
    set_many(defaults)


def _migrate_customers() -> None:
    """Add first_name / family_name columns for separate name storage, in a
    backward-compatible way that only alters the table when the columns are
    missing."""
    for col in ("first_name", "family_name"):
        existing = query_one(
            "SELECT COUNT(*) AS c FROM pragma_table_info('customers') WHERE name=?", (col,)
        )
        if int(existing["c"]) == 0:
            execute(f"ALTER TABLE customers ADD COLUMN {col} TEXT")

def _seed_templates() -> None:
    """Seed the notification templates for the first run only."""
    from app.services.notifications import DEFAULT_TEMPLATES, ensure_templates

    for event, body in DEFAULT_TEMPLATES.items():
        execute(
            "INSERT OR IGNORE INTO notification_templates(event_type, body, is_active, updated_at) "
            "VALUES (?,?,?,?)",
            (event, body, 1, now_iso()),
        )


def now_iso():
    from app.db import query_one
    row = query_one("SELECT datetime('now') AS t")
    return row["t"]
    from app.db import query_one

    row = query_one("SELECT datetime('now') AS t")
    return row["t"]
