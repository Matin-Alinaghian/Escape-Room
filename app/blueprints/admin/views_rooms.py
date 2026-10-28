"""Admin → room (scenario) management: list, create, edit, publish, delete."""
from __future__ import annotations

import json
import re

from flask import abort, flash, redirect, render_template, request, url_for

from app.auth import current_admin, require_permission
from app.blueprints.admin import bp
from app.db import execute, now_iso, query, query_one
from app.services.audit import log_action
from app.services.images import UploadError, delete_image, save_image

STATUSES = ("draft", "published", "inactive", "archived")
GENRES = ("وحشت", "جادو", "زندان", "نفرین", "جنایی", "علمی-تخیلی", "کودک")



INT_FIELDS = {
    "horror_intensity": (1, 5),
    "difficulty": (1, 5),
    "duration_minutes": (15, 300),
    "min_players": (1, 30),
    "max_players": (1, 30),
    "price_per_person": (0, 100_000_000),
    "price_per_group": (0, 100_000_000),
    "discount_percent": (0, 90),
    "sort_order": (0, 999),
}

TEXT_FIELDS = (
    "title",
    "short_description",
    "story",
    "address",
    "age_restriction",
    "safety",
    "rules",
    "video_url",
    "seo_title",
    "seo_description",
)


def _slugify(text: str, fallback: str = "room") -> str:
    """Build a URL slug that keeps Persian letters (they are valid in URLs)."""
    text = (text or "").strip().lower()
    text = re.sub(r"[^\w؀-ۿ\-]+", "-", text, flags=re.UNICODE)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text or fallback


def _unique_slug(base: str, scenario_id: int | None = None) -> str:
    slug = _slugify(base)
    candidate, n = slug, 2
    while True:
        row = query_one("SELECT id FROM scenarios WHERE slug=?", (candidate,))
        if row is None or (scenario_id is not None and row["id"] == scenario_id):
            return candidate
        candidate = f"{slug}-{n}"
        n += 1


def _parse_faqs(raw: str) -> list[dict]:
    """Accept either a JSON array or simple ``سؤال | پاسخ`` lines."""
    raw = (raw or "").strip()
    if not raw:
        return []
    if raw.startswith("["):
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return [
                    {"q": str(d.get("q", "")).strip(), "a": str(d.get("a", "")).strip()}
                    for d in data
                    if isinstance(d, dict)
                ]
        except ValueError:
            pass
    faqs = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        q, _, a = line.partition("|")
        if q.strip():
            faqs.append({"q": q.strip(), "a": a.strip()})
    return faqs


def _int_field(name: str, default: int = 0) -> int:
    raw = (request.form.get(name) or "").strip()
    if raw == "":
        return default
    # Accept Persian numerals from the admin form.
    raw = raw.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"مقدار «{name}» باید عدد باشد.") from None
    low, high = INT_FIELDS.get(name, (0, 10**9))
    if not low <= value <= high:
        raise ValueError(f"مقدار «{name}» باید بین {low} و {high} باشد.")
    return value


def _collect(scenario_id: int | None = None) -> dict:
    data: dict = {}
    for name in TEXT_FIELDS:
        data[name] = (request.form.get(name) or "").strip()

    for name in INT_FIELDS:
        data[name] = _int_field(name)

    genre_raw = (request.form.get("genre") or "").strip()
    data["genre"] = genre_raw if genre_raw in GENRES else "وحشت"

    status_raw = (request.form.get("status") or "draft").strip()
    data["status"] = status_raw if status_raw in STATUSES else "draft"

    data["featured"] = 1 if request.form.get("featured") else 0

    min_p, max_p = data["min_players"], data["max_players"]
    if max_p < min_p:
        raise ValueError("حداکثر تعداد نفرات باید بزرگ‌تر یا مساوی حداقل باشد.")
    if not data["title"]:
        raise ValueError("عنوان اتاق الزامی است.")

    data["faqs"] = json.dumps(_parse_faqs(request.form.get("faqs", "")), ensure_ascii=False)
    data["slug"] = _unique_slug(
        (request.form.get("slug") or data["title"]).strip(), scenario_id
    )
    return data


@bp.route("/rooms")
@require_permission("scenarios.manage")
def rooms_index():
    status = request.args.get("status", "")
    where = ""
    args: tuple = ()
    if status in STATUSES:
        where = "WHERE status=?"
        args = (status,)
    rows = query(
        f"SELECT * FROM scenarios {where} ORDER BY sort_order, title", args
    )
    return render_template(
        "admin/rooms/index.html",
        rows=rows,
        statuses=STATUSES,
        genres=GENRES,
        active_status=status,
        stats={
            "total": len(rows),
            "published": sum(1 for r in rows if r["status"] == "published"),
            "draft": sum(1 for r in rows if r["status"] == "draft"),
        },
    )


def _faqs_text(raw: str | None) -> str:
    """Render stored FAQ JSON back into editable ``سؤال | پاسخ`` lines."""
    try:
        items = json.loads(raw or "[]")
    except ValueError:
        return ""
    return "\n".join(f"{i.get('q', '')} | {i.get('a', '')}" for i in items if isinstance(i, dict))


@bp.route("/rooms/new", methods=["GET", "POST"])
@require_permission("scenarios.manage")
def rooms_new():
    if request.method == "POST":
        try:
            data = _collect()
        except ValueError as exc:
            flash(str(exc), "error")
            return render_template(
                "admin/rooms/edit.html",
                row=None,
                form=request.form,
                faqs_text="",
                statuses=STATUSES,
                genres=GENRES,
            )
        cols = ",".join(data)
        marks = ",".join("?" * len(data))
        cur = execute(
            f"INSERT INTO scenarios({cols}) VALUES ({marks})",
            tuple(data.values()),
        )
        log_action(
            "scenario.create",
            data["title"],
            entity_type="scenario",
            entity_id=cur.lastrowid,
        )
        flash("اتاق ساخته شد.", "success")
        return redirect(url_for("admin.rooms_index"))
    return render_template(
        "admin/rooms/edit.html",
        row=None,
        form={},
        faqs_text="",
        statuses=STATUSES,
        genres=GENRES,
    )


@bp.route("/rooms/<int:scenario_id>/edit", methods=["GET", "POST"])
@require_permission("scenarios.manage")
def rooms_edit(scenario_id: int):
    row = query_one("SELECT * FROM scenarios WHERE id=?", (scenario_id,))
    if row is None:
        abort(404)

    if request.method == "POST":
        try:
            data = _collect(scenario_id)
        except ValueError as exc:
            flash(str(exc), "error")
            return render_template(
                "admin/rooms/edit.html",
                row=row,
                form=request.form,
                faqs_text=_faqs_text(row["faqs"]),
                statuses=STATUSES,
                genres=GENRES,
            )
        sets = ", ".join(f"{k}=?" for k in data)
        execute(
            f"UPDATE scenarios SET {sets}, updated_at=? WHERE id=?",
            (*data.values(), now_iso(), scenario_id),
        )
        log_action(
            "scenario.update", data["title"], entity_type="scenario", entity_id=scenario_id
        )
        flash("تغییرات ذخیره شد.", "success")
        return redirect(url_for("admin.rooms_index"))
    return render_template(
        "admin/rooms/edit.html",
        row=row,
        form={},
        faqs_text=_faqs_text(row["faqs"]),
        statuses=STATUSES,
        genres=GENRES,
    )


@bp.route("/rooms/<int:scenario_id>/status", methods=["POST"])
@require_permission("scenarios.manage")
def rooms_status(scenario_id: int):
    row = query_one("SELECT title, status FROM scenarios WHERE id=?", (scenario_id,))
    if row is None:
        abort(404)
    new = (request.form.get("status") or "").strip()
    if new not in STATUSES:
        flash("وضعیت نامعتبر است.", "error")
    else:
        execute(
            "UPDATE scenarios SET status=?, updated_at=? WHERE id=?",
            (new, now_iso(), scenario_id),
        )
        log_action(
            f"scenario.status.{new}", row["title"], entity_type="scenario", entity_id=scenario_id
        )
        flash("وضعیت به‌روزرسانی شد.", "success")
    return redirect(request.referrer or url_for("admin.rooms_index"))


@bp.route("/rooms/<int:scenario_id>/delete", methods=["POST"])
@require_permission("scenarios.manage")
def rooms_delete(scenario_id: int):
    row = query_one("SELECT * FROM scenarios WHERE id=?", (scenario_id,))
    if row is None:
        abort(404)

    used = query_one(
        "SELECT COUNT(*) AS c FROM reservations WHERE scenario_id=?", (scenario_id,)
    )
    if used and int(used["c"]) > 0:
        # Never destroy booking history: archive instead.
        execute(
            "UPDATE scenarios SET status='archived', updated_at=? WHERE id=?",
            (now_iso(), scenario_id),
        )
        log_action(
            "scenario.archive",
            f"{row['title']} (has reservations)",
            entity_type="scenario",
            entity_id=scenario_id,
        )
        flash(
            "این اتاق رزرو ثبت‌شده دارد، بنابراین حذف نشد و به «بایگانی» منتقل شد.",
            "info",
        )
    else:
        execute("DELETE FROM scenarios WHERE id=?", (scenario_id,))
        delete_image(row["cover_image"])
        log_action(
            "scenario.delete", row["title"], entity_type="scenario", entity_id=scenario_id
        )
        flash("اتاق حذف شد.", "success")
    return redirect(url_for("admin.rooms_index"))


# --------------------------------------------------------------------------
# Gallery images
# --------------------------------------------------------------------------


@bp.route("/rooms/<int:scenario_id>/images", methods=["GET", "POST"])
@require_permission("media.manage")
def rooms_images(scenario_id: int):
    row = query_one("SELECT * FROM scenarios WHERE id=?", (scenario_id,))
    if row is None:
        abort(404)

    if request.method == "POST":
        try:
            crop = None
            if request.form.get("crop_enabled") == "1":
                crop = (
                    float(request.form.get("crop_x", "0")),
                    float(request.form.get("crop_y", "0")),
                    float(request.form.get("crop_w", "0")),
                    float(request.form.get("crop_h", "0")),
                )
            saved = save_image(
                request.files["image"],
                folder="scenarios",
                prefix=f"s{scenario_id}",
                crop=crop,
            )
        except UploadError as exc:
            flash(str(exc), "error")
        except (KeyError, ValueError):
            flash("فایل یا مختصات برش نامعتبر است.", "error")
        else:
            alt = (request.form.get("alt") or row["title"]).strip()[:200]
            execute(
                "INSERT INTO scenario_images(scenario_id, path, alt, sort_order) "
                "VALUES (?,?,?,COALESCE((SELECT MAX(sort_order)+1 FROM scenario_images WHERE scenario_id=?),0))",
                (scenario_id, saved.url, alt, scenario_id),
            )
            log_action(
                "scenario.image.add", saved.filename, entity_type="scenario", entity_id=scenario_id
            )
            flash("تصویر بارگذاری شد.", "success")
        return redirect(url_for("admin.rooms_images", scenario_id=scenario_id))

    images = query(
        "SELECT * FROM scenario_images WHERE scenario_id=? ORDER BY sort_order, id",
        (scenario_id,),
    )
    return render_template(
        "admin/rooms/images.html", row=row, images=images, scenario_id=scenario_id
    )


@bp.route("/images/<int:image_id>/cover", methods=["POST"])
@require_permission("media.manage")
def images_make_cover(image_id: int):
    img = query_one("SELECT * FROM scenario_images WHERE id=?", (image_id,))
    if img is None:
        abort(404)
    execute("UPDATE scenario_images SET is_cover=0 WHERE scenario_id=?", (img["scenario_id"],))
    execute("UPDATE scenario_images SET is_cover=1 WHERE id=?", (image_id,))
    execute(
        "UPDATE scenarios SET cover_image=?, updated_at=? WHERE id=?",
        (img["path"], now_iso(), img["scenario_id"]),
    )
    log_action("scenario.image.cover", img["path"], entity_type="scenario", entity_id=img["scenario_id"])
    flash("تصویر کاور انتخاب شد.", "success")
    return redirect(url_for("admin.rooms_images", scenario_id=img["scenario_id"]))


@bp.route("/images/<int:image_id>/delete", methods=["POST"])
@require_permission("media.manage")
def images_delete(image_id: int):
    img = query_one("SELECT * FROM scenario_images WHERE id=?", (image_id,))
    if img is None:
        abort(404)
    execute("DELETE FROM scenario_images WHERE id=?", (image_id,))
    scenario = query_one("SELECT cover_image FROM scenarios WHERE id=?", (img["scenario_id"],))
    if scenario and scenario["cover_image"] == img["path"]:
        execute(
            "UPDATE scenarios SET cover_image=NULL, updated_at=? WHERE id=?",
            (now_iso(), img["scenario_id"]),
        )
    delete_image(img["path"])
    log_action("scenario.image.delete", img["path"], entity_type="scenario", entity_id=img["scenario_id"])
    flash("تصویر حذف شد.", "success")
    return redirect(url_for("admin.rooms_images", scenario_id=img["scenario_id"]))