"""Generate the site's placeholder artwork with Pillow.

The reference design is a dark horror look: near-black rooms, a single red
light source, deep vignette. Real photography is not shipped with the repo, so
this script paints deterministic, theme-consistent images that match the
palette exactly. Re-run it any time to regenerate:

    python scripts/make_images.py
"""
from __future__ import annotations

import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

BASE = Path(__file__).resolve().parent.parent
IMG = BASE / "app" / "static" / "img"
SCEN = BASE / "app" / "static" / "uploads" / "scenarios"

BLACK = (4, 4, 6)
RED = (255, 59, 48)
DEEP_RED = (150, 18, 14)
COLD = (150, 165, 185)

THEMES = {
    "hero": ("bimaristan", 900, 1150),
    "polaroid-dardas": ("dardas", 520, 520),
    "polaroid-khorshid": ("khorshid", 520, 520),
    "polaroid-kalid": ("kalid", 520, 520),
    "polaroid-raz": ("raz", 520, 520),
    "atakh-bimaristan": ("bimaristan", 900, 900),
    "atakh-jadugar": ("jadugar", 900, 900),
    "atakh-zendan": ("zendan", 900, 900),
    "atakh-nefari": ("nefari", 900, 900),
    "atakh-khoon": ("khoon", 900, 900),
}


# --------------------------------------------------------------------------
# Painting helpers
# --------------------------------------------------------------------------


def _gradient(size, top, bottom):
    w, h = size
    img = Image.new("RGB", (1, h))
    px = img.load()
    for y in range(h):
        t = y / max(h - 1, 1)
        px[0, y] = (
            int(top[0] + (bottom[0] - top[0]) * t),
            int(top[1] + (bottom[1] - top[1]) * t),
            int(top[2] + (bottom[2] - top[2]) * t),
        )
    return img.resize((w, h), Image.BICUBIC)


def _radial_glow(img, cx, cy, radius, color, strength=1.0):
    """Additive red light source."""
    w, h = img.size
    glow = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(glow)
    steps = 26
    for i in range(steps, 0, -1):
        r = radius * i / steps
        a = int(255 * strength * (1 - i / steps) ** 2.2)
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=a)
    glow = glow.filter(ImageFilter.GaussianBlur(radius * 0.16))
    tint = Image.new("RGB", (w, h), color)
    return Image.blend(img, Image.composite(tint, img, glow), 0.85)


def _vignette(img, power=1.5, amount=0.92, target_mean=9.0, contrast=1.0):
    """Darken the edges of the frame, leaving the centre untouched.

    The mask must be BRIGHT in the middle and DARK at the rim, because
    Image.composite(img, dark, mask) keeps `img` where the mask is bright.
    Painting the shrinking ellipses brightest-last (small ellipse = centre = 255)
    is what makes the falloff point the right way round.
    """
    w, h = img.size
    mask = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(mask)
    steps = 40
    for i in range(steps, 0, -1):
        t = i / steps
        inset_x = w * 0.5 * t * 0.9
        inset_y = h * 0.5 * t * 0.9
        d.ellipse(
            (inset_x - w * 0.25, inset_y - h * 0.25, w - inset_x + w * 0.25, h - inset_y + h * 0.25),
            fill=int(255 * (1 - t)),
        )
    mask = mask.filter(ImageFilter.GaussianBlur(min(w, h) * 0.06))
    dark = Image.new("RGB", (w, h), (0, 0, 0))
    return _grade(Image.blend(img, Image.composite(img, dark, mask), amount),
                  target_mean=target_mean, contrast=contrast)


def _grain(img, amount=9):
    w, h = img.size
    noise = Image.effect_noise((w, h), 26).convert("L")
    noise = noise.point(lambda v: int((v - 128) * 0.6 + 128))
    return Image.blend(img, Image.merge("RGB", (noise, noise, noise)), amount / 100)


def _floor_and_walls(size, wall_top=(16, 10, 10), wall_bottom=(4, 3, 4)):
    """One-point perspective room: back wall + floor + side walls."""
    w, h = size
    img = _gradient((w, h), wall_top, wall_bottom)
    d = ImageDraw.Draw(img)

    vx, vy = w * 0.5, h * 0.56
    corners = [(0, h), (w, h), (w, vy - h * 0.06), (vx + w * 0.16, vy - h * 0.10),
               (vx - w * 0.16, vy - h * 0.10), (0, vy - h * 0.04)]
    d.polygon(corners, fill=(10, 6, 7))
    # Ceiling shadow
    d.polygon([(0, 0), (w, 0), (w, vy - h * 0.04), (vx + w * 0.16, vy - h * 0.10),
               (vx - w * 0.16, vy - h * 0.10)], fill=(7, 4, 5))
    return img


def _door(img, cx, top, bottom, width, color=DEEP_RED):
    w, h = img.size
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rectangle((cx - width, top, cx + width, bottom), fill=color + (235,))
    d.rectangle((cx - width, top, cx + width, top + (bottom - top) * 0.06),
                fill=(0, 0, 0, 120))
    img = Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")
    return img


def _silhouette(img, cx, base, height, width_ratio=0.30):
    """Standing human silhouette — the recurring motif of the reference."""
    w, h = img.size
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    hw = height * width_ratio
    head_r = hw * 0.34
    top = base - height
    # head
    d.ellipse((cx - head_r, top, cx + head_r, top + head_r * 2.1), fill=(2, 2, 3, 255))
    # shoulders + torso
    shoulder_y = top + head_r * 2.6
    d.polygon(
        [
            (cx - hw, shoulder_y + height * 0.06),
            (cx - hw * 0.62, shoulder_y - height * 0.01),
            (cx + hw * 0.62, shoulder_y - height * 0.01),
            (cx + hw, shoulder_y + height * 0.06),
            (cx + hw * 0.72, base - height * 0.32),
            (cx + hw * 0.66, base),
            (cx - hw * 0.66, base),
            (cx - hw * 0.72, base - height * 0.32),
        ],
        fill=(2, 2, 3, 255),
    )
    layer = layer.filter(ImageFilter.GaussianBlur(1.2))
    return Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")


def _window(img, x, y, w, h, mullions=2):
    x, y, w, h = int(x), int(y), int(w), int(h)
    bar = max(2, w // 24)
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rectangle((x, y, x + w, y + h), fill=(255, 120, 90, 40))
    for i in range(1, mullions + 1):
        mx = x + w * i // (mullions + 1)
        d.line((mx, y, mx, y + h), fill=(2, 2, 3, 255), width=bar)
    d.line((x, y + h // 2, x + w, y + h // 2), fill=(2, 2, 3, 255), width=bar)
    d.rectangle((x, y, x + w, y + h), outline=(2, 2, 3, 255), width=bar)
    return Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")


def _dust(img, count=260, seed=7):
    rnd = random.Random(seed)
    w, h = img.size
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for _ in range(count):
        x, y = rnd.uniform(0, w), rnd.uniform(0, h)
        r = rnd.uniform(0.6, 2.4)
        a = int(rnd.uniform(20, 90))
        d.ellipse((x - r, y - r, x + r, y + r), fill=(255, 190, 170, a))
    layer = layer.filter(ImageFilter.GaussianBlur(0.8))
    return Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")


def _grade(img, target_mean=26.0, contrast=1.18):
    """Cinematic grade: lift to the reference's tonal range, then add contrast.

    Measured from docs/reference-homepage.png: the composed page averages
    rgb(42, 22, 22) with strong highlights (std ~65). The old pipeline pinned
    every scene to a near-black mean of ~9 with std ~13, which rendered as a
    flat black void on the page. Hue ratios are preserved: every channel is
    scaled by the same factor before the contrast S-curve.
    """
    px = list(img.getdata())
    if not px:
        return img
    mean = sum(sum(c) for c in px) / (len(px) * 3.0)
    if mean <= 0.01:
        return img
    scale = target_mean / mean
    # Soft-clip the highlights so bright pools do not blow out to flat white.
    if scale > 1.0:
        limit = 255.0
        scale = min(scale, (limit * 0.55) / max(max(max(c) for c in px), 1))
    table = [min(255, int(v * scale + 0.5)) for v in range(256)]
    img = img.point(table * 3)
    if contrast != 1.0:
        # Pivot around mid-grey so shadows stay rich instead of washing out.
        img = ImageEnhance.Contrast(img).enhance(contrast)
    return img


def _tone(img, target_mean=9.0):
    """Legacy brightness pin (kept for callers that need the near-black look)."""
    return _grade(img, target_mean=target_mean, contrast=1.0)


def _blood_splatter(img, seed=1, count=7, cx=None, cy=None, spread=1.0):
    """Paint wet blood: a main pool plus satellite droplets and run-off trails."""
    rnd = random.Random(seed)
    w, h = img.size
    cx = w * 0.5 if cx is None else cx
    cy = h * 0.5 if cy is None else cy
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    # Main pool: overlapping ellipses so the edge is irregular, not a circle.
    for _ in range(count):
        r = rnd.uniform(w * 0.05, w * 0.16) * spread
        ox = rnd.uniform(-w * 0.07, w * 0.07) * spread
        oy = rnd.uniform(-h * 0.05, h * 0.05) * spread
        shade = rnd.randint(96, 150)
        d.ellipse((cx + ox - r, cy + oy - r * rnd.uniform(0.5, 0.9),
                   cx + ox + r, cy + oy + r * rnd.uniform(0.5, 0.9)),
                  fill=(shade, 10, 12, rnd.randint(210, 245)))

    # Satellite droplets, thinning outwards.
    for _ in range(int(count * 9)):
        ang = rnd.uniform(0, math.tau)
        dist = rnd.uniform(w * 0.06, w * 0.30) * spread
        x, y = cx + math.cos(ang) * dist, cy + math.sin(ang) * dist
        r = rnd.uniform(w * 0.003, w * 0.016) * spread
        d.ellipse((x - r, y - r * 0.8, x + r, y + r * 0.8),
                  fill=(rnd.randint(110, 165), 12, 14, rnd.randint(190, 235)))

    # Run-off trails: a drip bead joined to a thin streak.
    for _ in range(int(count * 3)):
        x = cx + rnd.uniform(-w * 0.16, w * 0.16) * spread
        y0 = cy + rnd.uniform(-h * 0.02, h * 0.06) * spread
        length = rnd.uniform(h * 0.10, h * 0.34) * spread
        wdt = max(1, int(w * rnd.uniform(0.004, 0.011)))
        d.line((x, y0, x + rnd.uniform(-w * 0.012, w * 0.012), y0 + length),
               fill=(126, 12, 14, 205), width=wdt)
        br = wdt * rnd.uniform(1.1, 1.9)
        d.ellipse((x - br, y0 + length - br, x + br, y0 + length + br), fill=(140, 14, 16, 225))

    layer = layer.filter(ImageFilter.GaussianBlur(max(0.6, w * 0.0016)))
    return Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")


def _blood_wall_drips(img, seed=2, count=16):
    """Vertical streaks bleeding down a wall - the 'someone was here' motif."""
    rnd = random.Random(seed)
    w, h = img.size
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for _ in range(count):
        x = rnd.uniform(0, w)
        y0 = rnd.uniform(0, h * 0.18)
        length = rnd.uniform(h * 0.12, h * 0.52)
        wdt = max(1, int(w * rnd.uniform(0.003, 0.010)))
        d.line((x, y0, x + rnd.uniform(-w * 0.008, w * 0.008), y0 + length),
               fill=(118, 11, 13, rnd.randint(120, 190)), width=wdt)
        # a bead where the drip finally stopped
        br = wdt * rnd.uniform(1.3, 2.4)
        d.ellipse((x - br, y0 + length - br, x + br, y0 + length + br),
                  fill=(132, 13, 15, 200))
    layer = layer.filter(ImageFilter.GaussianBlur(max(0.8, w * 0.002)))
    return Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")


def _scratches(img, seed=3, count=14):
    rnd = random.Random(seed)
    w, h = img.size
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for _ in range(count):
        x = rnd.uniform(0, w)
        y = rnd.uniform(0, h * 0.5)
        length = rnd.uniform(h * 0.05, h * 0.3)
        d.line((x, y, x + rnd.uniform(-6, 6), y + length),
               fill=(255, 120, 100, int(rnd.uniform(10, 35))), width=1)
    return Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")


# --------------------------------------------------------------------------
# Scenes
# --------------------------------------------------------------------------


def scene_hero(size):
    """Doorway of red light with a lone figure — the reference hero."""
    w, h = size
    img = _floor_and_walls(size)
    img = _door(img, w * 0.5, h * 0.20, h * 0.74, w * 0.15)
    img = _radial_glow(img, w * 0.5, h * 0.34, w * 0.52, RED, 0.62)
    img = _silhouette(img, w * 0.5, h * 0.735, h * 0.44, 0.22)
    # pool of light on the floor
    img = _radial_glow(img, w * 0.5, h * 0.78, w * 0.34, (200, 30, 24), 0.35)
    img = _scratches(img, seed=11)
    img = _dust(img, 320, seed=5)
    img = _grain(img, 11)
    return _vignette(img, amount=0.95, target_mean=38.0, contrast=1.22)


def scene_bimaristan(size):
    """Hospital corridor with a lit doorway at the end."""
    w, h = size
    img = _floor_and_walls(size, (18, 10, 11), (3, 3, 4))
    for i in range(6):
        t = i / 6
        inset_x = w * (0.06 + t * 0.34)
        inset_y = h * (0.10 + t * 0.32)
        d = ImageDraw.Draw(img)
        d.rectangle((inset_x, inset_y, w - inset_x, h - inset_y),
                    outline=(9, 5, 6), width=max(2, int(w * 0.006)))
    img = _door(img, w * 0.5, h * 0.30, h * 0.68, w * 0.11)
    img = _radial_glow(img, w * 0.5, h * 0.47, w * 0.40, RED, 0.5)
    img = _window(img, w * 0.13, h * 0.34, w * 0.16, h * 0.24)
    img = _silhouette(img, w * 0.62, h * 0.79, h * 0.30, 0.20)
    img = _dust(img, 220, seed=21)
    img = _grain(img, 10)
    return _vignette(img, target_mean=26.0, contrast=1.18)


def scene_jadugar(size):
    """Tower interior: arch, moonlit window, floating rune light."""
    w, h = size
    img = _gradient(size, (14, 12, 22), (3, 3, 5))
    d = ImageDraw.Draw(img)
    d.ellipse((w * 0.28, h * 0.10, w * 0.72, h * 0.66), fill=(24, 24, 34))
    img = _window(img, w * 0.34, h * 0.16, w * 0.32, h * 0.40, mullions=2)
    img = _radial_glow(img, w * 0.5, h * 0.34, w * 0.40, (170, 40, 40), 0.42)
    img = _radial_glow(img, w * 0.5, h * 0.74, w * 0.30, RED, 0.5)
    d = ImageDraw.Draw(img)
    # arch silhouette in front
    d.rectangle((w * 0.06, h * 0.52, w * 0.94, h), fill=(5, 4, 6))
    d.ellipse((w * 0.06, h * 0.40, w * 0.94, h * 0.66), fill=(5, 4, 6))
    img = _silhouette(img, w * 0.5, h * 0.95, h * 0.44, 0.20)
    img = _dust(img, 260, seed=33)
    img = _grain(img, 10)
    return _vignette(img, target_mean=26.0, contrast=1.18)


def scene_zendan(size):
    """Cell: barred door with red light bleeding through."""
    w, h = size
    img = _floor_and_walls(size, (20, 12, 10), (3, 3, 4))
    img = _door(img, w * 0.5, h * 0.16, h * 0.80, w * 0.20, color=(120, 16, 12))
    img = _radial_glow(img, w * 0.5, h * 0.48, w * 0.34, RED, 0.55)
    d = ImageDraw.Draw(img)
    for i in range(7):
        x = w * (0.30 + i * 0.066)
        d.rectangle((x, h * 0.16, x + w * 0.016, h * 0.80), fill=(3, 3, 4))
    d.rectangle((w * 0.29, h * 0.44, w * 0.71, h * 0.50), fill=(3, 3, 4))
    img = _radial_glow(img, w * 0.5, h * 0.92, w * 0.30, (180, 28, 22), 0.35)
    img = _scratches(img, seed=41)
    img = _grain(img, 12)
    return _vignette(img, target_mean=26.0, contrast=1.18)


def scene_nefari(size):
    """Cursed house: red moon behind trees and a figure on the porch."""
    w, h = size
    img = _gradient(size, (26, 8, 10), (3, 2, 4))
    img = _radial_glow(img, w * 0.62, h * 0.26, w * 0.42, (220, 40, 34), 0.72)
    d = ImageDraw.Draw(img)
    d.ellipse((w * 0.50, h * 0.12, w * 0.76, h * 0.38), fill=(255, 96, 80))
    # bare branches
    rnd = random.Random(5)
    for _ in range(9):
        x0 = rnd.uniform(0, w)
        d.line((x0, h * 0.62, x0 + rnd.uniform(-w * 0.25, w * 0.25), h * rnd.uniform(0.16, 0.42)),
               fill=(4, 3, 5), width=max(2, int(w * 0.010)))
    # house mass
    d.polygon([(w * 0.08, h), (w * 0.08, h * 0.58), (w * 0.5, h * 0.40),
               (w * 0.92, h * 0.58), (w * 0.92, h)], fill=(5, 4, 6))
    d.rectangle((w * 0.42, h * 0.66, w * 0.58, h * 0.94), fill=(150, 30, 24))
    img = _radial_glow(img, w * 0.5, h * 0.80, w * 0.22, RED, 0.55)
    img = _silhouette(img, w * 0.5, h * 0.94, h * 0.28, 0.16)
    img = _grain(img, 12)
    return _vignette(img, target_mean=26.0, contrast=1.18)


def scene_khoon(size):
    """Blood room: a butcher's corridor, walls running, floor pooled."""
    w, h = size
    img = _floor_and_walls(size, (22, 8, 9), (3, 2, 3))

    # Tiled back wall, then blood running down it.
    d = ImageDraw.Draw(img)
    for row in range(6):
        for col in range(8):
            x0 = w * (0.20 + col * 0.075)
            y0 = h * (0.14 + row * 0.062)
            d.rectangle((x0, y0, x0 + w * 0.070, y0 + h * 0.057),
                        outline=(16, 7, 8), width=max(1, int(w * 0.002)))
    img = _blood_wall_drips(img, seed=17, count=22)

    # Hanging carcass silhouette over the far end of the corridor.
    img = _door(img, w * 0.5, h * 0.26, h * 0.62, w * 0.10, color=(86, 14, 12))
    img = _radial_glow(img, w * 0.5, h * 0.44, w * 0.40, (190, 30, 24), 0.44)

    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    hook_x, hook_y = w * 0.62, h * 0.16
    d.line((hook_x, hook_y, hook_x, h * 0.24), fill=(60, 60, 66, 255), width=max(2, int(w * 0.005)))
    d.ellipse((hook_x - w * 0.035, h * 0.22, hook_x + w * 0.035, h * 0.30), fill=(4, 3, 4, 255))
    d.polygon([(hook_x - w * 0.045, h * 0.28), (hook_x + w * 0.045, h * 0.28),
               (hook_x + w * 0.060, h * 0.50), (hook_x - w * 0.060, h * 0.50)], fill=(4, 3, 4, 255))
    d.line((hook_x - w * 0.050, h * 0.46, hook_x - w * 0.105, h * 0.54), fill=(4, 3, 4, 255),
           width=max(2, int(w * 0.016)))
    d.line((hook_x + w * 0.050, h * 0.46, hook_x + w * 0.105, h * 0.54), fill=(4, 3, 4, 255),
           width=max(2, int(w * 0.016)))
    layer = layer.filter(ImageFilter.GaussianBlur(1.4))
    img = Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")

    # Blood on the floor: a wide pool plus a trail leading the eye to the door.
    img = _blood_splatter(img, seed=29, count=9, cx=w * 0.47, cy=h * 0.76, spread=1.15)
    img = _blood_splatter(img, seed=63, count=3, cx=w * 0.66, cy=h * 0.90, spread=0.5)
    img = _blood_splatter(img, seed=91, count=2, cx=w * 0.30, cy=h * 0.86, spread=0.35)

    img = _silhouette(img, w * 0.36, h * 0.80, h * 0.34, 0.18)
    img = _dust(img, 200, seed=77)
    img = _grain(img, 12)
    return _vignette(img, amount=0.94, target_mean=30.0, contrast=1.20)


def scene_dardas(size):
    """Polaroid 1 — a dark office desk lit from one side."""
    w, h = size
    img = _gradient(size, (18, 12, 9), (3, 3, 4))
    d = ImageDraw.Draw(img)
    d.rectangle((0, h * 0.62, w, h), fill=(8, 5, 6))
    d.rectangle((w * 0.10, h * 0.58, w * 0.90, h * 0.66), fill=(24, 16, 12))
    d.rectangle((w * 0.20, h * 0.66, w * 0.24, h), fill=(12, 8, 8))
    d.rectangle((w * 0.76, h * 0.66, w * 0.80, h), fill=(12, 8, 8))
    img = _radial_glow(img, w * 0.74, h * 0.44, w * 0.40, (220, 90, 40), 0.55)
    img = _window(img, w * 0.58, h * 0.16, w * 0.30, h * 0.34, mullions=1)
    img = _grain(img, 14)
    return _vignette(img, amount=0.9, target_mean=24.0, contrast=1.15)


def scene_khorshid(size):
    """Polaroid 2 — a long corridor vanishing into red light."""
    w, h = size
    img = _floor_and_walls(size)
    for i in range(5):
        t = i / 5
        x0 = w * (0.10 + t * 0.30)
        y0 = h * (0.12 + t * 0.30)
        d = ImageDraw.Draw(img)
        d.rectangle((x0, y0, w - x0, h - y0), outline=(10, 6, 6), width=max(2, int(w * 0.007)))
    img = _door(img, w * 0.5, h * 0.38, h * 0.66, w * 0.07)
    img = _radial_glow(img, w * 0.5, h * 0.52, w * 0.26, RED, 0.72)
    img = _silhouette(img, w * 0.44, h * 0.80, h * 0.24, 0.18)
    img = _grain(img, 13)
    return _vignette(img, amount=0.9, target_mean=24.0, contrast=1.15)


def scene_kalid(size):
    """Polaroid 3 — a key hanging against red light."""
    w, h = size
    img = _gradient(size, (24, 6, 6), (3, 2, 3))
    img = _radial_glow(img, w * 0.5, h * 0.5, w * 0.5, RED, 0.62)
    d = ImageDraw.Draw(img)
    cx, cy = w * 0.5, h * 0.52
    r = w * 0.17
    d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(20, 14, 10), width=int(w * 0.045))
    d.line((cx, cy + r, cx, cy + r + w * 0.26), fill=(20, 14, 10), width=int(w * 0.038))
    for i in range(3):
        y = cy + r + w * (0.06 + i * 0.07)
        d.line((cx, y, cx + w * 0.10, y), fill=(20, 14, 10), width=int(w * 0.032))
    img = _dust(img, 200, seed=61)
    img = _grain(img, 12)
    return _vignette(img, amount=0.9, target_mean=24.0, contrast=1.15)


def scene_raz(size):
    """Polaroid 4 — a puzzle box on a red-lit table."""
    w, h = size
    img = _gradient(size, (14, 9, 10), (3, 3, 4))
    d = ImageDraw.Draw(img)
    d.rectangle((0, h * 0.60, w, h), fill=(11, 7, 7))
    img = _radial_glow(img, w * 0.5, h * 0.34, w * 0.42, (230, 70, 40), 0.5)
    d = ImageDraw.Draw(img)
    cx, cy, s = w * 0.5, h * 0.58, w * 0.30
    top = [(cx - s, cy - s * 0.55), (cx, cy - s * 0.85), (cx + s, cy - s * 0.55), (cx, cy - s * 0.25)]
    d.polygon([(cx - s, cy - s * 0.55), (cx, cy - s * 0.25), (cx, cy + s * 0.55), (cx - s, cy + s * 0.25)], fill=(30, 14, 10))
    d.polygon([(cx + s, cy - s * 0.55), (cx, cy - s * 0.25), (cx, cy + s * 0.55), (cx + s, cy + s * 0.25)], fill=(20, 9, 7))
    d.polygon(top, fill=(44, 20, 13))
    d.line([(cx, cy - s * 0.85), (cx, cy - s * 0.25)], fill=(255, 140, 90), width=3)
    img = _dust(img, 180, seed=71)
    img = _grain(img, 13)
    return _vignette(img, amount=0.9, target_mean=24.0, contrast=1.15)


SCENES = {
    "hero": scene_hero,
    "bimaristan": scene_bimaristan,
    "jadugar": scene_jadugar,
    "zendan": scene_zendan,
    "nefari": scene_nefari,
    "khoon": scene_khoon,
    "dardas": scene_dardas,
    "khorshid": scene_khorshid,
    "kalid": scene_kalid,
    "raz": scene_raz,
}


# Rooms that get a photo gallery, mapped to the scene that paints them.
GALLERY_ROOMS = {
    "atakh-bimaristan": "bimaristan",
    "atakh-jadugar": "jadugar",
    "atakh-zendan": "zendan",
    "atakh-nefari": "nefari",
    "atakh-khoon": "khoon",
}

# Gallery output size, and the source size each scene is rendered at before
# cropping. Rendering wide and cropping gives genuinely different framings of
# the same painted scene instead of four identical files.
GALLERY_SIZE = (1200, 800)
GALLERY_SOURCE = (1800, 1200)

# Focal windows (left, top, right, bottom) as fractions of the source image.
# Each one frames a different part of the room so the gallery reads as a set.
GALLERY_FRAMES = (
    (0.00, 0.00, 0.67, 0.67),
    (0.33, 0.00, 1.00, 0.67),
    (0.00, 0.33, 0.67, 1.00),
    (0.33, 0.33, 1.00, 1.00),
)


def _crop_frame(img, frame):
    """Crop ``img`` to the fractional window ``frame``, then resize to GALLERY_SIZE."""
    w, h = img.size
    left, top, right, bottom = frame
    box = (int(w * left), int(h * top), int(w * right), int(h * bottom))
    crop = img.crop(box)
    # Scale to cover the target box, then centre-crop so framing is stable
    # regardless of how much extra width the window has.
    tw, th = GALLERY_SIZE
    scale = max(tw / crop.width, th / crop.height)
    crop = crop.resize((max(tw, int(crop.width * scale)), max(th, int(crop.height * scale))), Image.LANCZOS)
    x = (crop.width - tw) // 2
    y = (crop.height - th) // 2
    return crop.crop((x, y, x + tw, y + th))


def generate_galleries() -> None:
    """Write four framed gallery images per room into the scenario upload dir."""
    for slug, theme in GALLERY_ROOMS.items():
        source = SCENES[theme](GALLERY_SOURCE)
        for index, frame in enumerate(GALLERY_FRAMES, start=1):
            art = _crop_frame(source, frame)
            # A touch of extra grain per frame so they don't look like re-crops
            # of one another when viewed side by side.
            art = _grain(art, 6 + index * 2)
            target = SCEN / f"gallery-{slug}-{index}.jpg"
            art.save(target, "JPEG", quality=86, optimize=True, progressive=True)
            print(f"wrote {target.relative_to(BASE)}  ({GALLERY_SIZE[0]}x{GALLERY_SIZE[1]})")


def main() -> None:
    IMG.mkdir(parents=True, exist_ok=True)
    SCEN.mkdir(parents=True, exist_ok=True)

    for name, (theme, w, h) in THEMES.items():
        art = SCENES[theme]((w, h))
        target = IMG / f"{name}.jpg"
        art.save(target, "JPEG", quality=88, optimize=True, progressive=True)
        thumb = art.copy()
        thumb.thumbnail((520, 520), Image.LANCZOS)
        thumb.save(IMG / f"{name}_thumb.jpg", "JPEG", quality=80, optimize=True)

        # Scenario covers also live under uploads so admin uploads can replace them.
        if name.startswith("atakh-"):
            art.save(SCEN / f"{name}.jpg", "JPEG", quality=88, optimize=True)
        print(f"wrote {target.relative_to(BASE)}  ({w}x{h})")

    generate_galleries()

    print(f"\n{len(THEMES)} images written to {IMG}")


if __name__ == "__main__":
    main()