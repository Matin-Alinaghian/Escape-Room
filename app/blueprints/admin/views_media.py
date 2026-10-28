"""Admin → media library: upload, browse and delete images."""
from __future__ import annotations

from flask import abort, flash, redirect, render_template, request, url_for

from app.auth import require_permission
from app.blueprints.admin import bp
from app.db import execute, query, query_one
from app.services.audit import log_action
from app.services.images import UploadError, delete_image, save_image, thumb_url
from pathlib import Path


@bp.route("/media")
@require_permission("media.manage")
def media_index():
    q = (request.args.get("q") or "").strip()
    args: tuple = ()
    clause = ""
    if q:
        clause = "WHERE filename LIKE ?"
        args = (f"%{q}%",)
    rows = query(
        f"SELECT m.*, a.username AS uploader FROM media m "
        f"LEFT JOIN admins a ON a.id=m.uploaded_by {clause} "
        "ORDER BY m.created_at DESC LIMIT 300",
        args,
    )
    rows = [
        {**dict(r), "thumb": thumb_url(r["path"]) or r["path"]}
        for r in rows
    ]
    total_bytes = query_one("SELECT COALESCE(SUM(size_bytes),0) AS s FROM media")["s"]
    return render_template(
        "admin/media/index.html", rows=rows, search=q, total_bytes=int(total_bytes or 0)
    )


@bp.route("/media", methods=["POST"])
@require_permission("media.manage")
def media_upload():
    from app.auth import current_admin

    files = request.files.getlist("images") or []
    saved, errors = 0, []
    for f in files:
        if not f or not f.filename:
            continue
        try:
            info = save_image(f, folder="media", prefix="media")
        except UploadError as exc:
            errors.append(str(exc))
            continue
        execute(
            "INSERT INTO media(filename, path, mime, size_bytes, uploaded_by, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (
                info.filename,
                info.url,
                "image/jpeg",
                _size_of(info.path),
                current_admin()["id"],
                _now(),
            ),
        )
        saved += 1

    if saved:
        log_action("media.upload", f"{saved} فایل", entity_type="media")
        flash(f"{saved} تصویر بارگذاری شد.", "success")
    for err in errors:
        flash(err, "error")
    if not saved and not errors:
        flash("فایلی انتخاب نشده است.", "error")
    return redirect(url_for("admin.media_index"))


@bp.route("/media/<int:media_id>/crop", methods=["POST"])
@require_permission("media.manage")
def media_crop(media_id: int):
    """Crop a stored media image server-side (x, y, w, h in natural pixels)."""
    row = query_one("SELECT * FROM media WHERE id=?", (media_id,))
    if row is None:
        abort(404)
    try:
        crop = (
            float(request.form.get("x", "")),
            float(request.form.get("y", "")),
            float(request.form.get("w", "")),
            float(request.form.get("h", "")),
        )
    except ValueError:
        flash("مختصات برش نامعتبر است.", "error")
        return redirect(url_for("admin.media_index"))
    if min(crop) < 0 or crop[2] < 10 or crop[3] < 10:
        flash("ابعاد ناحیه برش باید حداقل ۱۰ پیکسل باشد.", "error")
        return redirect(url_for("admin.media_index"))

    from app.auth import current_admin

    from app.services.images import crop_stored_image

    try:
        info = crop_stored_image(row["path"], crop)
    except UploadError as exc:
        flash(str(exc), "error")
        return redirect(url_for("admin.media_index"))

    execute(
        "INSERT INTO media(filename, path, mime, size_bytes, uploaded_by, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (
            info.filename,
            info.url,
            "image/jpeg",
            Path(info.path).stat().st_size,
            current_admin()["id"],
            _now(),
        ),
    )
    log_action(
        "media.crop",
        f"{row['filename']} -> {info.filename} ({int(crop[2])}x{int(crop[3])})",
        entity_type="media",
        entity_id=media_id,
    )
    flash("نسخه برش‌خورده ساخته شد (اصلی حفظ شد).", "success")
    return redirect(url_for("admin.media_index"))


@bp.route("/media/<int:media_id>/delete", methods=["POST"])
@require_permission("media.manage")
def media_delete(media_id: int):
    row = query_one("SELECT * FROM media WHERE id=?", (media_id,))
    if row is None:
        abort(404)
    execute("DELETE FROM media WHERE id=?", (media_id,))
    delete_image(row["path"])
    log_action("media.delete", row["filename"], entity_type="media", entity_id=media_id)
    flash("فایل حذف شد.", "success")
    return redirect(url_for("admin.media_index"))


def _size_of(url: str) -> int:
    from pathlib import Path

    from flask import current_app

    name = url.rsplit("/", 1)[-1]
    path = Path(current_app.config["UPLOAD_DIR"]) / "media" / name
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _now() -> str:
    from app.db import now_iso

    return now_iso()