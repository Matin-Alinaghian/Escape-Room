"""Seed demo reviews (approved) so every published room shows a real star rating.

Run:  PYTHONIOENCODING=utf-8 python scripts/seed_reviews.py

Why this exists
---------------
The owner wants visitors to see a star average next to price/difficulty above
the booking button — but a fresh database has no reviews, so every room would
show nothing. This writes a handful of plausible Persian reviews per published
room, each wired through the same tables production uses (customer -> slot ->
completed reservation -> approved review), so averages, counts and the
moderation queue all behave exactly as they would for real traffic.

Every room is reviewed by different people, so every room has to carry *its
own* average: بيمارستان 5.0، جادوگر 4.75، زندان 4.5، نفرین 4.6 — not one
number repeated four times, which is what the first version of this file
produced and what the owner (correctly) read as a bug.

Everything here is idempotent: reservation codes are deterministic, the unique
index on (reservation_id, scenario_id) keeps reviews from doubling up, a re-run
fills what is missing, and a seeded review whose rating or text has changed is
updated to match this file.
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from app.db import execute, query, query_one

# slug -> [(rating, title, body, reviewer name, phone, days ago), ...]
# The ratings are per room, deliberately: each room has its own crowd and its
# own average (5.0 / 4.75 / 4.5 / 4.6), so the star meter on a card really does
# describe that room. Everything stays >= 4, as a working shop expects.
REVIEWS: dict[str, list[tuple]] = {
    # 5,5,5,5 -> 5.0
    "atakh-bimaristan": [
        (5, "صحنه‌پردازی بی‌نظیر",
         "ورود به بخش اورژانس واقعاً مو به مو طراحی شده بود؛ صدای مانیتور قلب و نورهای "
         "چشمک‌زن حس یک بیمارستان متروکه را می‌داد. معماها هم یکنواخت نبود.",
         "سارا محمدی", "09123000001", 9),
        (5, "برای تیم ۴ نفره عالی",
         "چهار نفری رفتیم و هیچ‌کس بی‌کار نماند؛ هر کداممان یک بخش از کار را بر عهده "
         "داشتیم. گریم بازیگر هم واقعاً ترسناک بود.",
         "رضا کریمی", "09123000002", 16),
        (5, "معمای آخر نفس‌گیر بود",
         "سه معمای آخر سخت بود و حدود ده دقیقه رویشان ماندیم؛ همین چالش دقیقاً چیزی "
         "بود که دنبالش بودیم و لیدر هم حرفه‌ای کمکمان کرد تا فرار کنیم.",
         "نگار احمدی", "09123000003", 23),
        (5, "بهترین اتاق شهر",
         "قبلاً دو اتاق دیگر را هم رفته بودم، اما بیمارستان هم داستانش جدی‌تر بود و هم "
         "صحنه‌های ترسش حساب‌شده. قطعاً دوباره می‌آییم.",
         "امیر حسینی", "09123000004", 30),
    ],
    # 5,5,5,4 -> 4.75
    "atakh-jadugar": [
        (5, "جادو واقعی روی صحنه",
         "تکنیک‌های نمایشی واقعاً مثل جادو بود؛ تا آخر نفهمیدیم آن صحنه چطور اجرا شد. "
         "دکور چوبی و کتاب‌های کهن حس خیلی خوبی داشت.",
         "مریم رضایی", "09123000005", 10),
        (5, "راهنمای بازی فوق‌العاده",
         "لیدر ما حسابی غرق داستان بود و با لحن خودش ما را جلو می‌برد؛ معمای رمزگشایی "
         "کتاب‌ها هوشمندانه طراحی شده بود.",
         "حسین عبدی", "09123000006", 17),
        (5, "مناسب گروه‌های بزرگ",
         "شش نفره رفتیم و اصلاً شلوغ نشد؛ چون اتاق دو بخش جدا دارد، همه همزمان درگیر "
         "بودند و کسی معطل نماند.",
         "فاطمه نوری", "09123000007", 24),
        (4, "عالی، فقط کمی کوتاه",
         "فکر کردیم ۶۰ دقیقه کم است؛ همه‌چیز عالی بود ولی دلمان یک پرده دیگر می‌خواست. "
         "حتماً برای اتاق‌های بعدی می‌آییم.",
         "بهرام صادقی", "09123000008", 31),
    ],
    # 5,4,5,4 -> 4.5
    "atakh-zendan": [
        (5, "سخت و بی‌رحم",
         "فضاسازی صدا و نور طوری بود که واقعاً احساس زندانی بودن می‌کردیم. سخت‌ترین "
         "اتاقی که تا حالا رفتیم؛ از همین بابتش می‌ارزد.",
         "الهام کریمی", "09123000009", 11),
        (4, "پنج نفره، کمی شلوغ",
         "پنج نفری رفتیم و چون اتاق هم سخت است، بعضی جاها هم‌زمان روی یک معما می‌نشستیم. "
         "با این حال طراحی پازل‌ها واقعاً عالی بود.",
         "مهدی ظریف", "09123000010", 18),
        (5, "پایان غیرمنتظره",
         "چیزی که بیشتر از همه گرفت، پایان داستان بود؛ اصلاً حدس نمی‌زدیم. ارزش دوباره "
         "آمدن را دارد.",
         "زهرا موسوی", "09123000011", 25),
        (4, "تیم‌کاری را محک می‌زند",
         "به نظرم بهترین اتاق برای تیم‌هایی است که می‌خواهند ببینند چطور با هم کار می‌کنند. "
         "تقسیم کار کلید موفقیت بود، هرچند جاهایی هم منتظر هم می‌ماندیم.",
         "آرش فرهادی", "09123000012", 32),
    ],
    # 5,5,4,5,4 -> 4.6
    "atakh-nefari": [
        (5, "ترس از نوع واقعی",
         "داستان نفرین خانوادگی پرکشش بود و صحنه‌های ترسش بی‌موقع نبود؛ هر بار که فکر "
         "می‌کردیم آرام شدیم، اتفاقی می‌افتاد.",
         "نیلوفر اکبری", "09123000013", 12),
        (5, "اتاق ۵ ستاره",
         "از ورود تا خروج غافلگیر شدیم؛ چیدمان اتاق طوری بود که هیچ‌وقت حس تکراری بودن "
         "نمی‌گرفت.",
         "کامران شریفی", "09123000014", 19),
        (4, "فقط برای طرفداران ترس",
         "خیلی ترسناک است! ما نیمه‌راه چند بار از استرس خندیدیم. کیفیت ساخت بالاست، فقط "
         "قبلش بدانید چه انتظاری دارید.",
         "شیرین قاسمی", "09123000015", 26),
        (5, "راهنما حرفه‌ای و محترم",
         "بعد از بازی نشست و از پشت صحنه توضیح داد؛ همین نشان می‌دهد تیم حرفه‌ای است. "
         "برای تولد دوستم برنامه‌ریزی می‌کنیم دوباره بیاییم.",
         "پارسا نعمتی", "09123000016", 33),
        (4, "خوب بود، ولی به پای بیمارستان نمی‌رسد",
         "داستان نفرین جذاب بود و فضا واقعاً سنگین؛ فقط چند معمای میانی به نظرمان "
         "کمی تکراری آمد. در کل ارزش یک بار رفتن را دارد.",
         "سحر بهرامی", "09123000017", 38),
    ],
}


def _iso(d: date, hour: int) -> str:
    return datetime(d.year, d.month, d.day, hour, 0, tzinfo=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _customer(name: str, phone: str) -> int:
    row = query_one("SELECT id FROM customers WHERE phone=?", (phone,))
    if row:
        return int(row["id"])
    # No password_hash: these reviewers never sign in, and a null hash keeps
    # them out of the "phone already has a password" check during sign-up.
    cur = execute(
        "INSERT INTO customers(name, phone) VALUES (?,?)",
        (name, phone),
    )
    return int(cur.lastrowid)


def _slot(scenario_id: int, day: date, index: int) -> tuple[int, int]:
    """A slot on that day (generated from the schedules) + its price."""
    from app.services.availability import ensure_slots

    ensure_slots(scenario_id, day)
    rows = query(
        "SELECT id, price FROM slots WHERE scenario_id=? AND slot_date=? AND status='open' "
        "ORDER BY start_time",
        (scenario_id, day.isoformat()),
    )
    if not rows:
        # No schedule for that weekday: materialise one so the reservation is
        # still attached to a real slot row (nothing on the public calendar).
        start = 18 + index % 3
        cur = execute(
            "INSERT INTO slots(scenario_id, slot_date, start_time, end_time, capacity, price, status, generated) "
            "VALUES (?,?,?,?,?,?,'open',1)",
            (scenario_id, day.isoformat(), f"{start:02d}:00", f"{start + 1:02d}:00", 6, 0),
        )
        return int(cur.lastrowid), 0
    pick = rows[index % len(rows)]
    return int(pick["id"]), int(pick["price"] or 0)


def seed_reviews() -> dict[str, dict]:
    today = date.today()
    summary: dict[str, dict] = {}

    for slug, items in REVIEWS.items():
        scenario = query_one(
            "SELECT * FROM scenarios WHERE slug=?", (slug,)
        )
        if scenario is None:
            print(f"skip    {slug}: not in this database")
            continue
        if scenario["status"] != "published":
            print(f"skip    {slug}: status={scenario['status']}")
            continue

        for i, (rating, title, body, reviewer, phone, days_ago) in enumerate(items):
            day = today - timedelta(days=days_ago)
            customer_id = _customer(reviewer, phone)

            code = f"ER-REV{scenario['id']:02d}{i:02d}"
            res = query_one("SELECT id FROM reservations WHERE code=?", (code,))
            if res is None:
                from app.services.availability import compute_price

                slot_id, slot_price = _slot(int(scenario["id"]), day, i)
                participants = max(2, int(scenario["min_players"] or 2))
                unit, total = compute_price(scenario, slot_price, participants)
                cur = execute(
                    "INSERT INTO reservations(code, scenario_id, slot_id, customer_id, "
                    "participants, unit_price, total_price, status, source, slot_date, "
                    "start_time, created_at, updated_at, completed_at) "
                    "VALUES (?,?,?,?,?,?,?,'completed','web',?,?,?,?,?)",
                    (
                        code, scenario["id"], slot_id, customer_id,
                        participants, unit, total,
                        day.isoformat(), "18:00",
                        _iso(day, 15), _iso(day, 20), _iso(day, 20),
                    ),
                )
                reservation_id = int(cur.lastrowid)
            else:
                reservation_id = int(res["id"])

            existing = query_one(
                "SELECT * FROM reviews WHERE reservation_id=? AND scenario_id=?",
                (reservation_id, scenario["id"]),
            )
            if existing:
                # A seeded review that no longer matches this file (its rating
                # changed, its text was rewritten) is brought back in line, so
                # re-running the script is how the demo data is repaired. The
                # moderation status is left alone: rejecting a seeded review
                # stays the owner's decision.
                if (
                    int(existing["rating"]) != int(rating)
                    or (existing["title"] or "") != title
                    or (existing["body"] or "") != body
                ):
                    execute(
                        "UPDATE reviews SET rating=?, title=?, body=?, updated_at=? "
                        "WHERE id=?",
                        (rating, title, body, _iso(day, 21), existing["id"]),
                    )
                    print(f"update  {slug}: review #{existing['id']} → {rating}★")
                continue
            execute(
                "INSERT INTO reviews(reservation_id, scenario_id, customer_id, rating, "
                "title, body, status, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,'approved',?,?)",
                (
                    reservation_id, scenario["id"], customer_id, rating,
                    title, body, _iso(day, 21), _iso(day, 21),
                ),
            )

        from app.services.reviews import stats

        s = stats(int(scenario["id"]))
        summary[slug] = {"average": s["average"], "count": s["count"]}
        print(f"ok      {slug}: {s['count']} reviews, average {s['average']}")

    return summary


def main() -> int:
    app = create_app()
    with app.app_context():
        summary = seed_reviews()

    print("\nper-room averages (each room carries its own):")
    bad = 0
    for slug, s in summary.items():
        mark = "ok" if s["count"] else "FAIL"
        if mark == "FAIL":
            bad += 1
        print(f"  {mark}  {slug}: {s['average']} ({s['count']} reviews)")

    averages = [s["average"] for s in summary.values() if s["count"]]
    if len(set(averages)) != len(averages):
        print(
            "  FAIL  two rooms share an average — visitors would read one number "
            "on every card"
        )
        bad += 1

    if not summary:
        print("no published scenarios found", file=sys.stderr)
        return 1
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
