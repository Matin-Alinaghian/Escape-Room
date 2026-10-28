"""Safe image upload handling: extension + sniffing + size + thumbnails."""
from __future__ import annotations

import io
import secrets
from dataclasses import dataclass
from pathlib import Path

from flask import current_app
from PIL import Image, ImageOps

MAX_THUMB_WIDTH = 480
MAX_DISPLAY_WIDTH = 1600
CROP_QUALITY = 88  # cropped hero images get a slightly higher quality


@dataclass
class UploadedImage:
    url: str          # path relative to /static
    path: str         # absolute filesystem path
    width: int
    height: int
    filename: str


class UploadError(ValueError):
    """Raised with a Persian, user-presentable message."""


def _validate_stream(stream, filename: str) -> tuple[str, str]:
    ext = (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""
    allowed = current_app.config["ALLOWED_IMAGE_EXT"]
    if ext not in allowed:
        raise UploadError("پسوند فایل مجاز نیست. فرمت‌های JPG, PNG, WEBP, GIF مجازند.")
    return ext, f"image/{'jpeg' if ext in ('jpg', 'jpeg') else ext}"


def save_image(stream, *, folder: str = "scenarios", prefix: str = "img", crop: tuple[int, int, int, int] | None = None) -> UploadedImage:
    """Validate, re-encode, resize and store an uploaded image.

    Re-encoding through Pillow strips payloads (EXIF bombs, polyglot files)
    so only genuine pixels are ever written to disk.

    ``crop`` — optional ``(x, y, w, h)`` rectangle in the *natural* pixel
    coordinates of the uploaded (EXIF-oriented) image. When given, exactly
    that region is stored, so what the admin framed in the browser cropper is
    what lands on the server.
    """
    folder_path: Path = current_app.config["UPLOAD_DIR"] / folder
    folder_path.mkdir(parents=True, exist_ok=True)

    _validate_stream(stream, getattr(stream, "filename", "") or "")

    data = stream.read(current_app.config["MAX_IMAGE_BYTES"] + 1)
    if len(data) > current_app.config["MAX_IMAGE_BYTES"]:
        mb = current_app.config["MAX_IMAGE_BYTES"] // (1024 * 1024)
        raise UploadError(f"حجم فایل بیشتر از {mb} مگابایت است.")

    try:
        with Image.open(io.BytesIO(data)) as probe:
            probe.verify()
        with Image.open(io.BytesIO(data)) as im:
            # Read the format BEFORE exif_transpose: it returns a copy whose
            # .format is None, which would make every valid JPEG look invalid.
            fmt = (im.format or "").upper()
            im = ImageOps.exif_transpose(im)
            if fmt not in {"JPEG", "PNG", "WEBP", "GIF"}:
                raise UploadError("محتوای فایل تصویر معتبر نیست.")
            im.load()
            width, height = im.size
            if width < 200 or height < 200:
                raise UploadError("ابعاد تصویر باید حداقل ۲۰۰×۲۰۰ پیکسل باشد.")

            if crop is not None:
                try:
                    cx, cy, cw, ch = (int(round(v)) for v in crop)
                except (TypeError, ValueError):
                    raise UploadError("مختصات برش نامعتبر است.") from None
                # Clamp to the image: a stale client rect must never crash or
                # silently shift the frame.
                cx = max(0, min(cx, width - 1))
                cy = max(0, min(cy, height - 1))
                cw = max(50, min(cw, width - cx))
                ch = max(50, min(ch, height - cy))
                im = im.crop((cx, cy, cx + cw, cy + ch))
                width, height = im.size
            if width > MAX_DISPLAY_WIDTH:
                ratio = MAX_DISPLAY_WIDTH / float(width)
                im = im.resize((MAX_DISPLAY_WIDTH, int(height * ratio)), Image.LANCZOS)
                width, height = im.size

            # Flatten transparency onto black so PNG logos keep the dark look.
            if im.mode in ("RGBA", "LA", "P"):
                background = Image.new("RGB", im.size, (10, 10, 12))
                rgba = im.convert("RGBA")
                background.paste(rgba, mask=rgba.split()[-1])
                im = background
            im = im.convert("RGB")

            stem = f"{prefix}_{secrets.token_hex(8)}"
            filename = f"{stem}.jpg"
            dest = folder_path / filename
            quality = CROP_QUALITY if crop is not None else 84
            im.save(dest, "JPEG", quality=quality, optimize=True, progressive=True)

            # Web-sized thumbnail for grids/carousels.
            thumb = im.copy()
            thumb.thumbnail((MAX_THUMB_WIDTH, MAX_THUMB_WIDTH), Image.LANCZOS)
            thumb.save(folder_path / f"{stem}_thumb.jpg", "JPEG", quality=78, optimize=True)

            # Original aspect ratio retained; report final dimensions.
            return UploadedImage(
                url=f"/static/uploads/{folder}/{filename}",
                path=str(dest),
                width=width,
                height=height,
                filename=filename,
            )
    except UploadError:
        raise
    except Exception as exc:  # Pillow raises many error types
        raise UploadError("فایل تصویر قابل پردازش نیست.") from exc


def crop_stored_image(url: str, crop: tuple[int, int, int, int]) -> UploadedImage:
    """Re-crop an image already stored in the media library.

    ``crop`` is ``(x, y, w, h)`` in the natural pixels of the stored file.
    A NEW file is written (the original is preserved) and returned so the
    caller can register it in the ``media`` table.
    """
    if not url or not url.startswith("/static/uploads/"):
        raise UploadError("این فایل قابل برش نیست.")
    rel = url[len("/static/"):]
    target = (current_app.config["UPLOAD_DIR"] / Path(rel).relative_to("uploads")).resolve()
    upload_root = current_app.config["UPLOAD_DIR"].resolve()
    if not str(target).startswith(str(upload_root)) or not target.is_file():
        raise UploadError("فایل یافت نشد.")

    folder = target.parent.name
    try:
        with Image.open(target) as im:
            im = ImageOps.exif_transpose(im)
            im.load()
            width, height = im.size
            try:
                cx, cy, cw, ch = (int(round(v)) for v in crop)
            except (TypeError, ValueError):
                raise UploadError("مختصات برش نامعتبر است.") from None
            cx = max(0, min(cx, width - 1))
            cy = max(0, min(cy, height - 1))
            cw = max(50, min(cw, width - cx))
            ch = max(50, min(ch, height - cy))
            im = im.crop((cx, cy, cx + cw, cy + ch))
            if im.mode in ("RGBA", "LA", "P"):
                background = Image.new("RGB", im.size, (10, 10, 12))
                rgba = im.convert("RGBA")
                background.paste(rgba, mask=rgba.split()[-1])
                im = background
            im = im.convert("RGB")

            stem = f"crop_{secrets.token_hex(8)}"
            filename = f"{stem}.jpg"
            dest = target.parent / filename
            im.save(dest, "JPEG", quality=CROP_QUALITY, optimize=True, progressive=True)
            thumb = im.copy()
            thumb.thumbnail((MAX_THUMB_WIDTH, MAX_THUMB_WIDTH), Image.LANCZOS)
            thumb.save(target.parent / f"{stem}_thumb.jpg", "JPEG", quality=78, optimize=True)
            w, h = im.size
            return UploadedImage(
                url=f"/static/uploads/{folder}/{filename}",
                path=str(dest),
                width=w,
                height=h,
                filename=filename,
            )
    except UploadError:
        raise
    except Exception as exc:
        raise UploadError("برش تصویر ممکن نشد.") from exc


def delete_image(url: str | None) -> None:
    """Remove a previously stored upload (never touches anything else)."""
    if not url or not url.startswith("/static/uploads/"):
        return
    rel = url[len("/static/"):]
    target = (current_app.config["UPLOAD_DIR"] / Path(rel).relative_to("uploads")).resolve()
    upload_root = current_app.config["UPLOAD_DIR"].resolve()
    if str(target).startswith(str(upload_root)) and target.is_file():
        target.unlink(missing_ok=True)
        stem = target.stem
        (target.parent / f"{stem}_thumb.jpg").unlink(missing_ok=True)


def thumb_url(url: str | None) -> str | None:
    if not url or not url.startswith("/static/uploads/"):
        return url
    if not url.endswith(".jpg"):
        return url
    return url[:-4] + "_thumb.jpg"
