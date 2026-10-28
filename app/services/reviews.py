"""Review service: eligibility, submission and accurate rating statistics.

Only customers whose booking reached ``completed`` may review, and only once
per (reservation, scenario). Averages are computed from **approved** reviews
only — never synthesised. When nothing is approved the API reports
``has_reviews: False`` so the UI can show an empty state instead of a fake
average.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.db import execute, now_iso, query, query_one


class ReviewError(ValueError):
    def __init__(self, message: str, code: str = "invalid"):
        super().__init__(message)
        self.code = code


def eligibility(code: str) -> dict:
    """Can the holder of this booking reference leave a review?"""
    res = query_one(
        "SELECT r.id, r.status, r.scenario_id, s.title, s.slug "
        "FROM reservations r JOIN scenarios s ON s.id=r.scenario_id WHERE r.code=?",
        (code or "",),
    )
    if res is None:
        return {"eligible": False, "reason": "کد رهگیری یافت نشد.", "code": code}
    if res["status"] != "completed":
        return {
            "eligible": False,
            "reason": "فقط پس از پایان بازی امکان ثبت نظر وجود دارد.",
            "code": code,
            "status": res["status"],
        }
    existing = query_one(
        "SELECT id, status FROM reviews WHERE reservation_id=? AND scenario_id=?",
        (res["id"], res["scenario_id"]),
    )
    if existing:
        return {
            "eligible": False,
            "reason": "برای این رزرو قبلاً نظر ثبت شده است.",
            "code": code,
            "review_status": existing["status"],
        }
    return {
        "eligible": True,
        "code": code,
        "reservation_id": res["id"],
        "scenario_id": res["scenario_id"],
        "scenario_title": res["title"],
        "scenario_slug": res["slug"],
    }


def submit(code: str, rating: int, title: str, body: str) -> dict:
    info = eligibility(code)
    if not info["eligible"]:
        raise ReviewError(info["reason"], "not_eligible")

    try:
        rating = int(rating)
    except (TypeError, ValueError):
        raise ReviewError("امتیاز نامعتبر است.", "bad_rating")
    if not 1 <= rating <= 5:
        raise ReviewError("امتیاز باید بین ۱ تا ۵ باشد.", "bad_rating")

    body = (body or "").strip()
    title = (title or "").strip()
    if len(body) < 10:
        raise ReviewError("متن نظر باید حداقل ۱۰ کاراکتر باشد.", "too_short")
    if len(body) > 2000:
        raise ReviewError("متن نظر حداکثر ۲۰۰۰ کاراکتر است.", "too_long")

    from app.services.settings import get_setting

    auto_approve = bool(get_setting("review.auto_approve", False))
    status = "approved" if auto_approve else "pending"

    res = query_one("SELECT customer_id FROM reservations WHERE id=?", (info["reservation_id"],))
    cur = execute(
        "INSERT INTO reviews(reservation_id, scenario_id, customer_id, rating, title, body, status, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            info["reservation_id"], info["scenario_id"], res["customer_id"],
            rating, title[:120], body, status, now_iso(), now_iso(),
        ),
    )
    return {"id": cur.lastrowid, "status": status}


def stats(scenario_id: int | None = None) -> dict:
    """Accurate average, count and 1..5 distribution over approved reviews."""
    where = "WHERE status='approved'"
    args: tuple = ()
    if scenario_id is not None:
        where += " AND scenario_id=?"
        args = (scenario_id,)

    row = query_one(
        f"SELECT COUNT(*) AS cnt, AVG(rating) AS avg FROM reviews {where}", args
    )
    total = int(row["cnt"] or 0)
    avg = float(row["avg"] or 0)

    dist_rows = query(
        f"SELECT rating, COUNT(*) AS c FROM reviews {where} GROUP BY rating", args
    )
    dist = {i: 0 for i in range(1, 6)}
    for r in dist_rows:
        dist[int(r["rating"])] = int(r["c"])

    return {
        "has_reviews": total > 0,
        "count": total,
        "average": round(avg, 2) if total else 0.0,
        "distribution": {str(k): v for k, v in dist.items()},
        "percentages": {str(k): round(v * 100 / total) if total else 0 for k, v in dist.items()},
    }


def list_reviews(
    scenario_id: int | None = None,
    status: str = "approved",
    sort: str = "newest",
    limit: int = 20,
    offset: int = 0,
) -> list[dict]:
    where = ["r.status = ?"]
    args: list = [status]
    if scenario_id is not None:
        where.append("r.scenario_id=?")
        args.append(scenario_id)
    # Newest/oldest only: the "most positive"/"most negative" arrangements were
    # dropped from the public page, and keeping them here would leave a URL that
    # silently ordered reviews by a criterion nobody can ask for any more.
    order = {
        "newest": "r.created_at DESC",
        "oldest": "r.created_at ASC",
    }.get(sort, "r.created_at DESC")

    rows = query(
        "SELECT r.*, c.name AS customer_name, s.title AS scenario_title, s.slug AS scenario_slug "
        "FROM reviews r JOIN customers c ON c.id=r.customer_id "
        "JOIN scenarios s ON s.id=r.scenario_id "
        f"WHERE {' AND '.join(where)} ORDER BY {order} LIMIT ? OFFSET ?",
        (*args, int(limit), int(offset)),
    )
    out = []
    for row in rows:
        item = dict(row)
        # The reviewer's name exactly as they gave it. This used to publish
        # "سارا «م»" — first name plus the family initial — which reads like a
        # typo on a testimonial, so a signed review now carries the full name.
        name = (item.get("customer_name") or "").strip()
        item["customer_name"] = name or "مهمان"
        out.append(item)
    return out


def moderate(review_id: int, action: str, admin: dict, reply: str = "") -> None:
    from app.auth import has_permission
    from app.services.audit import log_action

    if not has_permission("reviews.moderate"):
        raise ReviewError("دسترسی لازم را ندارید.", "forbidden")

    row = query_one("SELECT * FROM reviews WHERE id=?", (review_id,))
    if row is None:
        raise ReviewError("نظر یافت نشد.", "not_found")

    mapping = {
        "approve": "approved",
        "reject": "rejected",
        "hide": "hidden",
        "pending": "pending",
    }
    if action == "delete":
        execute("DELETE FROM reviews WHERE id=?", (review_id,))
        log_action("review.delete", f"review #{review_id}", entity_type="review",
                   entity_id=review_id, admin=admin)
        return
    if action not in mapping:
        raise ReviewError("عملیات نامعتبر است.", "bad_action")

    execute(
        "UPDATE reviews SET status=?, updated_at=? WHERE id=?",
        (mapping[action], now_iso(), review_id),
    )
    log_action(f"review.{action}", f"review #{review_id} -> {mapping[action]}",
               entity_type="review", entity_id=review_id, admin=admin)

    if reply:
        reply_to = reply.strip()[:1000]
        execute(
            "UPDATE reviews SET admin_reply=?, replied_at=? WHERE id=?",
            (reply_to, now_iso(), review_id),
        )


def report(review_id: int, reason: str) -> None:
    execute(
        "UPDATE reviews SET reported=1, report_reason=?, updated_at=? WHERE id=?",
        (reason[:300], now_iso(), review_id),
    )
