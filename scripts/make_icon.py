"""The exobrain app icon: a low-poly brain in profile (grey facets lit from the upper left, white seams)
over the EXOBRAIN logotype, on a white macOS-style tile. The outline is the one the opening's particles gather into.

Usage: python scripts/make_icon.py <out_dir>   (needs Pillow, SciPy, numpy; writes icon-1024.png and exobrain.icns)
"""

import math
import random
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from scipy.spatial import Delaunay

S = 2048  # drawn at 2x, then reduced: smooth edges
random.seed(7)


def bezier(p0, p1, p2, p3, n=40):
    return [tuple((1 - t) ** 3 * a + 3 * (1 - t) ** 2 * t * b + 3 * (1 - t) * t ** 2 * c + t ** 3 * d
                  for a, b, c, d in zip(p0, p1, p2, p3)) for t in (i / n for i in range(n + 1))]


def brain_outline():
    """Cerebrum outline in the opening's 1000x720 stencil coordinates (front to the right)."""
    segs = [((905, 385), (930, 215), (790, 95), (610, 88)), ((610, 88), (420, 72), (215, 118), (150, 250)),
            ((150, 250), (108, 335), (135, 425), (215, 455)), ((215, 455), (268, 474), (330, 458), (385, 468)),
            ((385, 468), (430, 560), (615, 585), (725, 522)), ((725, 522), (785, 492), (828, 470), (862, 452)),
            ((862, 452), (892, 432), (908, 410), (905, 385))]
    pts = []
    for s in segs:
        pts += bezier(*s)[:-1]
    return pts


def even(poly, spacing, closed=True):
    """Points along a polyline at equal distances, so no stretch of the outline gets crowded."""
    pts = poly + ([poly[0]] if closed else [])
    out, carry = [], 0.0
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        seg = math.hypot(x2 - x1, y2 - y1)
        t = carry
        while t < seg:
            out.append((x1 + (x2 - x1) * t / seg, y1 + (y2 - y1) * t / seg))
            t += spacing
        carry = t - seg
    return out


def ellipse(cx, cy, rx, ry, rot, n=60):
    c, s = math.cos(rot), math.sin(rot)
    return [(cx + rx * math.cos(t) * c - ry * math.sin(t) * s, cy + rx * math.cos(t) * s + ry * math.sin(t) * c)
            for t in (2 * math.pi * i / n for i in range(n))]


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    # The tile: macOS-style rounded square with a soft shadow and a hairline edge.
    inset, radius = int(S * 100 / 1024), int(S * 185 / 1024)
    box = (inset, inset, S - inset, S - inset)
    shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((box[0], box[1] + 24, box[2], box[3] + 24), radius, fill=(0, 0, 0, 70))
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(40)))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(box, radius, fill=(255, 255, 255, 255), outline=(222, 222, 222, 255), width=4)

    # Map the stencil into the tile (the brain sits high; the logo goes underneath).
    scale = (box[2] - box[0]) * 0.70 / 800
    ox = S / 2 - 520 * scale
    oy = S * 0.43 - 360 * scale
    tr = lambda p: (ox + p[0] * scale, oy + p[1] * scale)  # noqa: E731
    cerebrum = [tr(p) for p in brain_outline()]
    cerebellum = [tr(p) for p in ellipse(262, 520, 118, 68, -0.12)]
    # Cerebrum and a brainstem growing out from inside it (the low-poly look reads best without the cerebellum).
    stem = [tr(p) for p in ((392, 420), (446, 420), (456, 612), (420, 620))]

    mask = Image.new("L", (S, S), 0)
    md = ImageDraw.Draw(mask)
    for poly in (cerebrum, stem):
        md.polygon(poly, fill=255)
    m = np.array(mask) > 0
    cmask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(cmask).polygon(cerebrum, fill=255)
    cm = np.array(cmask) > 0
    m_cerebrum = lambda p: cm[int(p[1]), int(p[0])]  # noqa: E731

    # Facet corners: the outline at even steps, a little jitter so the edge reads as cut, and points inside.
    step = S * 0.058
    nodes = [(x + random.uniform(-6, 6), y + random.uniform(-6, 6)) for x, y in even(cerebrum, step)]
    nodes += [p for p in even(stem, step * 0.8) if not m_cerebrum(p)]
    nodes += stem[2:]
    tries = 0
    while tries < 20000:
        tries += 1
        x, y = random.uniform(box[0], box[2]), random.uniform(box[1], box[3])
        if m[int(y), int(x)] and all((x - a) ** 2 + (y - b) ** 2 > (step * 0.95) ** 2 for a, b in nodes):
            nodes.append((x, y))
    P = np.array(nodes)

    # Soft shadow under the brain.
    lo = max(y for _, y in stem)
    sh = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(sh).ellipse((S * 0.30, lo + S * 0.035, S * 0.70, lo + S * 0.065), fill=(0, 0, 0, 60))
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(S * 0.012)))

    # Facets: light from the upper left, each face a slightly different grey.
    xs, ys = P[:, 0], P[:, 1]
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    facets = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    fd = ImageDraw.Draw(facets)
    for poly in (cerebrum, stem):  # a base tone under the facets, so no gap shows through at the edge
        fd.polygon(poly, fill=(185, 185, 185, 255))
    tris = []
    for tri in Delaunay(P).simplices:
        pts = [tuple(P[i]) for i in tri]
        cx, cy = sum(p[0] for p in pts) / 3, sum(p[1] for p in pts) / 3
        if not m[int(cy), int(cx)]:
            continue
        t = 0.55 * (cx - x0) / (x1 - x0) + 0.45 * (cy - y0) / (y1 - y0)  # 0 = upper left, 1 = lower right
        g = int(max(88, min(250, 246 - 150 * t + random.uniform(-26, 26))))
        fd.polygon(pts, fill=(g, g, g, 255))
        tris.append(pts)
    for pts in tris:  # hairline seams between the faces
        fd.line(pts + [pts[0]], fill=(255, 255, 255, 150), width=3)
    img.alpha_composite(facets)

    # The logo: EXOBRAIN in gothic, widely spaced.
    font = ImageFont.truetype("/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc", int(S * 0.066))
    text, track = "EXOBRAIN", S * 0.020
    widths = [font.getlength(ch) for ch in text]
    total = sum(widths) + track * (len(text) - 1)
    x, y = S / 2 - total / 2, lo + S * 0.085
    d = ImageDraw.Draw(img)
    for ch, w in zip(text, widths):
        d.text((x, y), ch, font=font, fill=(17, 17, 17, 255))
        x += w + track

    big = img.resize((1024, 1024), Image.LANCZOS)
    big.save(out / "icon-1024.png")
    iconset = out / "exobrain.iconset"
    iconset.mkdir(exist_ok=True)
    for size in (16, 32, 128, 256, 512):
        big.resize((size, size), Image.LANCZOS).save(iconset / f"icon_{size}x{size}.png")
        big.resize((size * 2, size * 2), Image.LANCZOS).save(iconset / f"icon_{size}x{size}@2x.png")
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(out / "exobrain.icns")], check=True)
    print(out / "icon-1024.png", out / "exobrain.icns")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "build/icon"))
