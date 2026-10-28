"""Paint the torn-paper divider used in the reference design.

The reference shows a ragged white paper edge separating sections — not a
smooth rule. This writes a wide PNG whose alpha channel is the torn silhouette,
so CSS can ``mask-image`` it and get a genuinely irregular edge.

    python scripts/make_tear.py
"""
from __future__ import annotations

import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

BASE = Path(__file__).resolve().parent.parent
IMG = BASE / "app" / "static" / "img"

W, H = 1600, 64
SEED = 1405


def _fibre(img: Image.Image, seed: int) -> Image.Image:
    """Speckle the paper so it does not read as flat white."""
    rnd = random.Random(seed)
    w, h = img.size
    layer = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(layer)
    for _ in range(2600):
        x, y = rnd.uniform(0, w), rnd.uniform(0, h)
        r = rnd.uniform(0.4, 1.9)
        d.ellipse((x - r, y - r, x + r, y + r), fill=int(rnd.uniform(25, 80)))
    layer = layer.filter(ImageFilter.GaussianBlur(0.5))
    white = Image.new("L", (w, h), 255)
    return layer


def build() -> Image.Image:
    rnd = random.Random(SEED)

    # A mask: white = keep, black = torn away.
    mask = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(mask)

    # Top edge: strongly ragged (fibre pull).
    top: list[tuple[float, float]] = []
    x = 0.0
    while x < W:
        step = rnd.uniform(14, 46)
        y = rnd.uniform(0, H * 0.42) * (0.45 + 0.55 * rnd.random())
        top.append((x, y))
        x += step
    top.append((W, rnd.uniform(0, H * 0.3)))

    # Bottom edge: a shallower wobble so the band keeps a readable body.
    bottom: list[tuple[float, float]] = []
    x = 0.0
    while x < W:
        step = rnd.uniform(40, 110)
        y = H - rnd.uniform(2, H * 0.30) * rnd.random()
        bottom.append((x, y))
        x += step
    bottom.append((W, H - rnd.uniform(2, 10)))

    polygon = top + list(reversed(bottom))
    d.polygon(polygon, fill=255)

    # Occasional deep tears that cut into the body of the band.
    for _ in range(14):
        cx = rnd.uniform(0, W)
        depth = rnd.uniform(H * 0.25, H * 0.75)
        width = rnd.uniform(6, 26)
        d.polygon(
            [
                (cx - width, 0),
                (cx - width * 0.35, depth),
                (cx + width * 0.45, depth * rnd.uniform(0.5, 0.9)),
                (cx + width, 0),
            ],
            fill=0,
        )

    mask = mask.filter(ImageFilter.GaussianBlur(0.7))

    # Paper body: near-white with a faint warm/grey fibre texture.
    fibre = _fibre(Image.new("RGB", (W, H), (255, 255, 255)), SEED + 1)
    paper = Image.new("RGB", (W, H), (247, 244, 238))
    paper = Image.composite(
        Image.new("RGB", (W, H), (225, 220, 212)), paper, fibre
    )

    out = paper.convert("RGBA")
    out.putalpha(mask)
    return out


def main() -> None:
    IMG.mkdir(parents=True, exist_ok=True)
    tear = build()
    dest = IMG / "tear.png"
    tear.save(dest, "PNG", optimize=True)

    # Also a vertically-flipped copy so the edge can be reused inverted.
    tear.transpose(Image.FLIP_TOP_BOTTOM).save(
        IMG / "tear-flipped.png", "PNG", optimize=True
    )
    print(f"wrote {dest.relative_to(BASE)} ({W}x{H})")
    print(f"wrote {(IMG / 'tear-flipped.png').relative_to(BASE)}")


if __name__ == "__main__":
    main()