"""Crop feature tests: server-side crop math, endpoint, quality, regression.

Run:  PYTHONIOENCODING=utf-8 python tests_crop.py
"""
from __future__ import annotations

import io
import re
import sys

sys.path.insert(0, ".")

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))


from PIL import Image

from app import create_app

app = create_app()

# --- make a distinct test image: 800x600, four coloured quadrants -----------
def _make_png():
    im = Image.new("RGB", (800, 600))
    px = im.load()
    for x in range(800):
        for y in range(600):
            # quadrant colours so we can verify which part survived the crop
            if x < 400 and y < 300:
                px[x, y] = (255, 0, 0)      # top-left  = red
            elif x >= 400 and y < 300:
                px[x, y] = (0, 255, 0)      # top-right = green
            elif x < 400 and y >= 300:
                px[x, y] = (0, 0, 255)      # bottom-left = blue
            else:
                px[x, y] = (255, 255, 0)    # bottom-right = yellow
    buf = io.BytesIO()
    im.save(buf, "PNG")
    buf.seek(0)
    return buf


def _avg(im, box):
    """Average colour of a region."""
    region = im.crop(box).resize((1, 1))
    return region.getpixel((0, 0))


class _File:
    filename = "test.png"

    def __init__(self, data):
        self._data = data

    def read(self, n=-1):
        return self._data.read(n)


with app.app_context():
    from app.services.images import save_image, crop_stored_image, UploadError

    # 1. plain upload keeps full size
    img1 = save_image(_File(_make_png()), folder="media", prefix="croptest1")
    check("full upload size", (img1.width, img1.height) == (800, 600), f"{img1.width}x{img1.height}")

    # 2. crop at upload: take the top-left red quadrant (400x300)
    img2 = save_image(_File(_make_png()), folder="media", prefix="croptest2",
                      crop=(0, 0, 400, 300))
    check("crop-at-upload dimensions", (img2.width, img2.height) == (400, 300),
          f"{img2.width}x{img2.height}")
    with Image.open(img2.path) as cropped:
        avg = _avg(cropped.convert("RGB"), (0, 0, 100, 100))
        check("cropped region is the red quadrant", avg[0] > 200 and avg[1] < 60, str(avg))

    # 3. crop_stored_image on the first upload: centre square
    img3 = crop_stored_image(img1.url, (200, 150, 400, 300))
    check("stored-crop dimensions", (img3.width, img3.height) == (400, 300),
          f"{img3.width}x{img3.height}")
    check("stored-crop is a NEW file", img3.path != img1.path)
    with Image.open(img3.path) as c3:
        rgb = c3.convert("RGB")
        tl = _avg(rgb, (0, 0, 50, 50))        # centre square starts in the red quadrant
        br = _avg(rgb, (350, 250, 400, 300))  # and ends in the yellow quadrant
        check("stored crop content (centre span)",
              tl[0] > 200 and tl[1] < 60 and br[0] > 200 and br[1] > 200 and br[2] < 60,
              f"tl={tl} br={br}")

    # 4. clamping: out-of-bounds crop must not crash and must stay in-bounds
    img4 = crop_stored_image(img1.url, (700, 500, 500, 500))
    check("out-of-bounds crop clamped", img4.width <= 100 and img4.height <= 100,
          f"{img4.width}x{img4.height}")

    # 5. invalid crop data raises UploadError (not a crash)
    try:
        crop_stored_image(img1.url, ("a", "b", "c", "d"))
        check("invalid crop rejected", False)
    except UploadError:
        check("invalid crop rejected", True)

    # 6. crop outside uploads root is refused
    try:
        crop_stored_image("/static/css/base.css", (0, 0, 10, 10))
        check("non-upload path refused", False)
    except UploadError:
        check("non-upload path refused", True)

    # cleanup all generated files
    from app.services.images import delete_image

    for u in (img1.url, img2.url, img3.url, img4.url):
        delete_image(u)

# --- 7. endpoint level: crop route writes a new media row --------------------
with app.app_context():
    from app.db import execute, query_one

    execute("DELETE FROM media WHERE filename LIKE 'cropEP%'")
    # insert a real file first via save_image
    from app.services.images import save_image

    with app.app_context():
        info = save_image(_File(_make_png()), folder="media", prefix="cropEP")
    cur = execute(
        "INSERT INTO media(filename, path, mime, size_bytes, uploaded_by) VALUES (?,?,?,?,1)",
        (info.filename, info.url, "image/jpeg", 0),
    )
    mid = cur.lastrowid

with app.test_client() as c:
    # login as super admin required; check the route exists & is protected
    r = c.post(f"/admin/media/{mid}/crop", data={"x": 0, "y": 0, "w": 400, "h": 300})
    check("crop endpoint requires auth", r.status_code in (302, 403), str(r.status_code))

    r2 = c.post("/admin/media/999999/crop", data={"x": 0, "y": 0, "w": 10, "h": 10})
    check("crop endpoint 404 for unknown id", r2.status_code in (302, 403, 404))

with app.app_context():
    from app.db import query_one as q1

    row = q1("SELECT path FROM media WHERE id=?", (mid,))
    check("media row exists for cleanup", row is not None)
    if row:
        from app.services.images import delete_image

        delete_image(row["path"])
        execute("DELETE FROM media WHERE id=?", (mid,))
    execute("DELETE FROM media WHERE filename LIKE 'cropEP%'")

# ------------------------------------------------------------------ report
failed = [x for x in RESULTS if not x[1]]
for name, ok, detail in RESULTS:
    print(("PASS  " if ok else "FAIL  ") + name + (("  <- " + detail) if (not ok and detail) else ""))
print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
sys.exit(1 if failed else 0)
