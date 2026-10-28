"""Generate the site's tiled background texture with Pillow.

The reference design sits on a near-black field (measured rgb(4.6, 4.2, 4.6))
where roughly 12% of the darkest pixels carry a red cast — i.e. faint blood
and grunge are present but must stay *underneath* the content, never loud.

This writes one seamless, tileable PNG:

    app/static/img/bg-blood.png

Every splatter is stamped nine times (its own tile plus the eight wrap-around
neighbours) so the edges match exactly and the image can repeat without seams.

Re-run it any time to regenerate:

    python scripts/make_background.py
"""
from __future__ import annotations

import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

BASE = Path(__file__).resolve().parent.parent
IMG = BASE / "app" / "static" / "img"

SIZE = 1600
SEED = 20260904

# Measured from the reference screenshot.
BASE_TOP = (7, 7, 10)
BASE_BOTTOM = (3, 3, 5)
BLOOD = (94, 10, 12)
BLOOD_DARK = (48, 5, 7)
GRUNGE = (16, 15, 18)

# One splatter: (x, y, radius, opacity, droplet_count)
SPLATTERS = 26
DROPS_PER_SPLATTER = 14


def _periodic_blur(img: Image.Image, radius: float) -> Image.Image:
    """Gaussian-blur without a seam.

    PIL reads outside the image as black, so blurring a tile directly bleeds
    darkening into all four edges and the repeat shows a hard grid. Blurring a
    3x3 tiling and cropping the middle tile makes the kernel wrap instead.
    """
    w, h = img.size
    tiled = Image.new("RGB", (w * 3, h * 3))
    for dy in range(3):
        for dx in range(3):
            tiled.paste(img, (dx * w, dy * h))
    return tiled.filter(ImageFilter.GaussianBlur(radius)).crop((w, h, w * 2, h * 2))


def _vertical_gradient(size: int) -> Image.Image:
    """A *periodic* vertical gradient: the top and bottom edges must match.

    A linear ramp would put BASE_BOTTOM next to BASE_TOP when the tile repeats,
    which is a guaranteed seam. A cosine completes a full cycle over the tile.
    """
    grad = Image.new("RGB", (1, size))
    px = grad.load()
    for y in range(size):
        t = y / (size - 1)
        k = (1 - math.cos(t * math.tau)) / 2  # 0 at y=0, 1 at y=h, back to 0
        px[0, y] = tuple(
            round(BASE_TOP[i] + (BASE_BOTTOM[i] - BASE_TOP[i]) * k) for i in range(3)
        )
    return grad.resize((size, size), Image.BILINEAR)


def _wrap(draw: ImageDraw.ImageDraw, size: int, fn) -> None:
    """Call ``fn(draw, ox, oy)`` for the tile and all eight wrap neighbours.

    This is what makes the result seamless: a splatter near an edge is also
    painted on the opposite side, so when the tile repeats the two halves join
    back into the same shape.
    """
    for dx in (-size, 0, size):
        for dy in (-size, 0, size):
            fn(draw, dx, dy)


def _blob(draw: ImageDraw.ImageDraw, ox: int, oy: int, cx: float, cy: float, r: float,
          color: tuple[int, int, int], opacity: float, rng: random.Random) -> None:
    """An irregular splatter: a wobbled polygon plus radiating droplets."""
    points = []
    lobes = rng.randint(9, 14)
    for i in range(lobes):
        angle = (i / lobes) * math.tau
        # Each lobe reaches a different distance, which is what makes the edge
        # look thrown rather than circular.
        reach = r * rng.uniform(0.55, 1.35)
        points.append((cx + math.cos(angle) * reach, cy + math.sin(angle) * reach))
    fill = tuple(int(c * opacity) for c in color)
    draw.polygon([(x + ox, y + oy) for x, y in points], fill=fill)

    for _ in range(DROPS_PER_SPLATTER):
        angle = rng.uniform(0, math.tau)
        dist = r * rng.uniform(1.2, 3.4)
        dr = r * rng.uniform(0.05, 0.22)
        dcx = cx + math.cos(angle) * dist
        dcy = cy + math.sin(angle) * dist
        d_op = opacity * rng.uniform(0.45, 0.95)
        d_fill = tuple(int(c * d_op) for c in color)
        draw.ellipse(
            [dcx - dr + ox, dcy - dr + oy, dcx + dr + ox, dcy + dr + oy],
            fill=d_fill,
        )


def _grunge(size: int, rng: random.Random) -> Image.Image:
    """Low-frequency mottling so large flat areas keep some tooth.

    Full-resolution noise, smoothed through the wrapping blur. The previous
    version tiled one small 40px random patch across the tile, and the
    repetition of that identical patch read as a visible grid of dark blocks
    (like prison cells) — so the patch is gone; real noise has no period and
    therefore no block grid, while the wrapping blur keeps it seamless.
    """
    noise = Image.effect_noise((size, size), 42).convert("RGB")
    # Two smoothing passes give low-frequency blotches instead of fine grain,
    # matching the original mottling intent without any repeating structure.
    noise = _periodic_blur(noise, 6).convert("L")
    return _periodic_blur(noise.convert("RGB"), 6).convert("L")


def _match_reference_luminance(img: Image.Image, target: float = 11.0) -> Image.Image:
    """Scale the tile onto a visible grunge range, then add gentle contrast.

    The reference page measures ~4.9 mean luminance *composed* — i.e. after the
    dark pages, photos and overlays sit on top. A background tile normalised to
    that same number renders as a featureless void (std ~1.3, measured). The
    tile therefore targets a slightly brighter mean with enough variance that
    the blood speckle actually reads under the content, plus a mild contrast
    pivot so the stains stay moody rather than milky.
    """
    px = img.load()
    total = 0
    count = 0
    for y in range(0, img.height, 8):
        for x in range(0, img.width, 8):
            r, g, b = px[x, y]
            total += (r + g + b) / 3
            count += 1
    current = total / count
    if current <= 0:
        return img
    factor = target / current

    lut = [min(255, int(round(i * factor))) for i in range(256)]
    out = img.point(lut * 3)
    # Gentle S-curve around the tile's own mean (Pillow pivots on the average
    # grey internally) so the red speckle keeps its bite without crushing the
    # near-black base to zero.
    from PIL import ImageEnhance

    return ImageEnhance.Contrast(out).enhance(1.35)


def build() -> Image.Image:
    rng = random.Random(SEED)
    base = _vertical_gradient(SIZE)

    # 1) Splatters, drawn largest and faintest first so small bright droplets
    #    land on top instead of being buried under a big stain.
    splat_layer = Image.new("RGB", (SIZE, SIZE), (0, 0, 0))
    draw = ImageDraw.Draw(splat_layer)
    spots = []
    for i in range(SPLATTERS):
        # Spread across the tile, with a bias toward the edges and corners —
        # that is where real splatter collects and it hides repeat seams.
        cx = rng.choice([rng.uniform(0, SIZE), rng.uniform(0, SIZE * 0.18),
                         rng.uniform(SIZE * 0.82, SIZE)])
        cy = rng.choice([rng.uniform(0, SIZE), rng.uniform(0, SIZE * 0.18),
                         rng.uniform(SIZE * 0.82, SIZE)])
        radius = rng.uniform(18, 74)
        opacity = rng.uniform(0.30, 0.62)
        color = BLOOD if i % 3 else BLOOD_DARK
        spots.append((cx, cy, radius, color, opacity))
    spots.sort(key=lambda s: -s[2])
    for cx, cy, radius, color, opacity in spots:
        _wrap(draw, SIZE, lambda d, ox, oy, c=(cx, cy, radius, color, opacity):
              _blob(d, ox, oy, c[0], c[1], c[2], c[3], c[4], random.Random(hash((c[0], c[1], SEED)) & 0xFFFF)))

    # Soften so the stain reads as absorbed into the floor, not painted on.
    splat_layer = _periodic_blur(splat_layer, 1.1)

    # 2) Composite the stain over the gradient. Kept light: the stain must sit
    #    under the content, so it is normalised onto the reference luminance
    #    at the end rather than left bright here.
    out = Image.blend(base, splat_layer, 0.62)

    # 3) Multiply in the mottling.
    mottle = _grunge(SIZE, rng)
    mottle_rgb = Image.merge("RGB", (mottle, mottle, mottle))
    out = Image.blend(out, Image.blend(out, mottle_rgb.point(lambda v: 90 + v // 6), 0.5), 0.35)

    # 4) Pull the whole tile onto the visible-grunge range.
    return _match_reference_luminance(out, target=11.0)


def seam_report(img: Image.Image) -> tuple[float, float]:
    """Compare the tile's wrap boundary against an ordinary interior boundary.

    In the tiled result the last column sits next to the first, so a seamless
    tile makes that boundary statistically ordinary. Returns (seam, interior).
    """
    w, h = img.size
    px = img.load()
    seam = sum(abs(px[0, y][c] - px[w - 1, y][c]) for y in range(h) for c in range(3)) / (h * 3)
    interior = sum(
        abs(px[x, y][c] - px[x + 1, y][c])
        for y in range(0, h, 3) for x in range(0, w - 1, 3) for c in range(3)
    ) / (len(range(0, h, 3)) * len(range(0, w - 1, 3)) * 3)
    return seam, interior


def main() -> None:
    IMG.mkdir(parents=True, exist_ok=True)
    img = build()

    seam, interior = seam_report(img)
    ratio = seam / interior if interior else 0.0
    verdict = "seamless" if ratio <= 2.2 else "VISIBLE SEAM"
    print(f"seam check: boundary {seam:.3f} vs interior {interior:.3f} "
          f"= {ratio:.2f}x -> {verdict}")
    if ratio > 2.2:
        raise SystemExit(f"background tile is not seamless ({ratio:.2f}x)")

    target = IMG / "bg-blood.png"
    img.save(target, "PNG", optimize=True)
    print(f"wrote {target.relative_to(BASE)}  ({img.width}x{img.height}, seamless tile)")

    # A 2x2 tiling preview: this is what actually ships on the page, and it is
    # the view in which a seam would show up.
    w, h = img.size
    preview = Image.new("RGB", (w * 2, h * 2))
    for dy in range(2):
        for dx in range(2):
            preview.paste(img, (dx * w, dy * h))
    prev = preview.resize((320, 320), Image.LANCZOS)
    prev_path = BASE / "docs" / "background-preview.png"
    prev_path.parent.mkdir(parents=True, exist_ok=True)
    prev.save(prev_path, "PNG")
    print(f"wrote {prev_path.relative_to(BASE)}  (320x320, 2x2 tiling preview)")


if __name__ == "__main__":
    main()
